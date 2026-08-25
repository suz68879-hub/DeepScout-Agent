"""LangGraph 图组装（spec §5.1 / agent-designs §0.2）。

热路径：chat_callback 恢复状态 → ainvoke 运行 interviewer 节点（占位）
并以 interrupt_after=["interviewer"] 中断。
冷路径：回合结束后 ainvoke(None, config) 从 interviewer 之后恢复执行
evaluator → planner → 条件边（finish → reporter / 否则 END 等待下一轮回调）。

Ruling R5：interviewer 节点在图内为占位——真实 LLM 流式生成在 HTTP 层执行
（interview_service），以持有流式生成器；状态写入经 aupdate_state 完成，
避免图内重复调用 LLM 计费。
Ruling R8：qa/finish 阶段不评分（候选人在提问，非作答）。

checkpointer 实现说明（偏离计划文本，环境适配）：langgraph 1.2.x 的同步
SqliteSaver 对异步图 API（aget_state/aupdate_state/ainvoke）直接抛
NotImplementedError，而热路径全程为异步调用——改用官方异步版
AsyncSqliteSaver（langgraph.checkpoint.sqlite.aio）+ aiosqlite 连接，
同库同表语义不变（spec §6）。aiosqlite 工作线程默认非 daemon，而 Python 3.13
进程退出时会等待非 daemon 线程；故连接后立即将工作线程置为 daemon，正常退出
由 FastAPI lifespan 调用 close_graph() 优雅关闭底层 sqlite3 连接。
"""
import logging
import os
import sqlite3

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from config import Config, settings
from rag.provider import get_retriever
from services.agent_llm import get_agent_llm
from .evaluator import evaluate_round
from .planner import generate_question
from .reporter import generate_report
from .stage_flow import PLANNING_STAGES, after_planner_route, maybe_advance_stage
from .state import InterviewState

_checkpoint_conn: aiosqlite.Connection | None = None
_checkpoint_pool: AsyncConnectionPool | None = None
_graph = None
logger = logging.getLogger(__name__)


class _DaemonConnection(aiosqlite.Connection):
    """aiosqlite 连接变体：工作线程置为 daemon（原因见模块 docstring）。

    线程在 __init__ 创建、首个 await/_execute 时才 start()，因此构造期设置
    daemon 是合法的（active 线程不允许改 daemon）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread.daemon = True


async def make_checkpointer(config: Config = settings):
    """按业务 backend 创建独立 checkpointer；PostgreSQL 不执行运行时建表。"""
    global _checkpoint_conn, _checkpoint_pool
    if config.STORAGE_BACKEND == "sqlite":
        db_path = config.DATABASE_PATH
        if os.path.dirname(db_path):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        _checkpoint_conn = await _DaemonConnection(lambda: sqlite3.connect(db_path), 64)
        return AsyncSqliteSaver(_checkpoint_conn)

    conninfo = config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        min_size=1,
        max_size=config.DATABASE_POOL_SIZE,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        async with pool.connection() as connection:
            await connection.execute("SELECT 1 FROM checkpoint_migrations LIMIT 1")
    except BaseException:
        await pool.close()
        raise
    _checkpoint_pool = pool
    return AsyncPostgresSaver(pool)


async def interviewer_node(state: InterviewState) -> dict:
    """热路径节点（占位，见模块注释 Ruling R5）。"""
    return {}


async def evaluator_node(state: InterviewState) -> dict:
    """冷路径：对上一轮回答评分；qa/finish 不评分（R8）；失败标记 status=failed。"""
    round_no = state.get("round_no", 0) + 1
    stage = state.get("stage", "intro")
    if stage in ("qa", "finish"):
        return {"scores": state.get("scores", []), "round_no": round_no}
    last_user = next(
        (m for m in reversed(state.get("messages", [])) if m["role"] == "user"), None
    )
    if last_user is None:
        return {"scores": state.get("scores", []), "round_no": round_no}
    rag = ""
    try:
        # top_k 参数仅 llamaindex provider 生效（volc_kb 忽略）
        rag = await get_retriever().aretrieve(last_user["content"])
    except Exception:
        logger.warning("Cold-path RAG retrieval failed; scoring without context")
    question = state.get("current_question") or {}
    score = await evaluate_round(
        position=state.get("position", "Java后端"),
        question=question.get("question_text", ""),
        reference_points=question.get("reference_points", []),
        answer=last_user["content"],
        rag_context=rag,
        llm=get_agent_llm("evaluator"),
        prompt_versions=state.get("prompt_versions"),
    )
    return {
        "scores": state.get("scores", []) + [score],
        "round_no": round_no,
        "rag_context": rag,
    }


async def planner_node(state: InterviewState) -> dict:
    """冷路径：阶段推进判定 + 出题（intro/qa/finish 不出题）。"""
    stage = state.get("stage", "intro")
    round_no = state.get("round_no", 0)
    projects = (state.get("resume") or {}).get("projects", [])
    project_count = len(projects) if projects else 1
    updates: dict = {}
    nxt = maybe_advance_stage(stage, round_no, project_count)
    if nxt:
        stage = nxt
        updates["round_no"] = 0
    updates["stage"] = stage
    if stage in PLANNING_STAGES:
        # 出题须用推进后的阶段（intro→technical 等切换轮出新阶段题，测试断言前缀）
        gen_state = dict(state)
        gen_state["stage"] = stage
        question = await generate_question(
            gen_state, state.get("rag_context", ""), get_agent_llm("planner")
        )
        asked = state.get("questions_asked", []) + [
            {"question_text": question.question_text, "topic": question.topic}
        ]
        updates["current_question"] = question.model_dump()
        updates["questions_asked"] = asked
    else:
        updates["current_question"] = None
    return updates


async def reporter_node(state: InterviewState) -> dict:
    """冷路径：生成报告（落库由 interview_service 在 invoke 完成后执行）。"""
    report = await generate_report(dict(state), get_agent_llm("reporter"))
    return {"report": report.model_dump()}


async def build_graph():
    builder = StateGraph(InterviewState)
    builder.add_node("interviewer", interviewer_node)
    builder.add_node("evaluator", evaluator_node)
    builder.add_node("planner", planner_node)
    builder.add_node("reporter", reporter_node)
    builder.add_edge(START, "interviewer")
    builder.add_edge("interviewer", "evaluator")
    builder.add_edge("evaluator", "planner")
    builder.add_conditional_edges(
        "planner",
        lambda state: after_planner_route(state.get("stage", "")),
        {"wait": END, "reporter": "reporter"},
    )
    builder.add_edge("reporter", END)
    return builder.compile(checkpointer=await make_checkpointer(), interrupt_after=["interviewer"])


async def init_graph():
    """在应用生命周期内初始化 LangGraph 单例。"""
    global _graph
    if _graph is None:
        _graph = await build_graph()
    return _graph


def get_graph():
    """返回已初始化的图；调用方必须运行在 FastAPI lifespan 之后。"""
    if _graph is None:
        raise RuntimeError("LangGraph 尚未初始化")
    return _graph


async def close_graph() -> None:
    """释放 checkpointer 连接并清空图单例。"""
    global _checkpoint_conn, _checkpoint_pool, _graph
    connection = _checkpoint_conn
    pool = _checkpoint_pool
    _checkpoint_conn = None
    _checkpoint_pool = None
    _graph = None
    try:
        if connection is not None:
            await connection.close()
    finally:
        if pool is not None:
            await pool.close()
