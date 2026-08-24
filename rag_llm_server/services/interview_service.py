"""会话编排：graph 的 HTTP 适配层（spec §5.5）。

热路径：定位/创建会话 → 写入用户消息 → 触发 interviewer 节点（占位）→
HTTP 层流式生成 → 回写助手消息。冷路径（评分/出题/报告）见文件尾 Task 13 段。
"""
import asyncio
import json
import logging
import time
import uuid

from agents.graph import get_graph
from agents.prompts.registry import registry
from config import settings
from services.storage import storage
from services.clock import utc_now

DEFAULT_POSITION = "Java后端"
logger = logging.getLogger(__name__)


def build_welcome_message(state: dict) -> str:
    """RTC 入房后主动播报的开场白；不调用 LLM，避免首屏等待。"""
    position = state.get("position") or DEFAULT_POSITION
    return f"你好，我是今天的面试官懂小智，负责你的{position}面试。请先用一分钟做一下自我介绍。"


def session_config(session_id: str) -> dict:
    """LangGraph config：thread_id = session_id（spec §5.1）。"""
    return {"configurable": {"thread_id": session_id}}


async def get_active_session(user_id: str) -> dict:
    """单用户 MVP：取最近 running 会话；无则自动创建（Ruling R2）。

    新会话岗位取自最近简历的 position_target（解析失败回落默认）。
    """
    running = await storage.session_list_running(user_id)
    if running:
        return running[0]
    resume = await storage.resume_latest(user_id)
    position = DEFAULT_POSITION
    if resume and resume.get("structured_json"):
        try:
            position = (
                json.loads(resume["structured_json"]).get("position_target")
                or DEFAULT_POSITION
            )
        except (TypeError, json.JSONDecodeError):
            pass
    return await storage.session_create(user_id, {
        "id": str(uuid.uuid4()),
        "resume_id": resume["id"] if resume else None,
        "position": position,
        "stage": "intro",
        "status": "running",
        "started_at": utc_now(),
        "ended_at": None,
    })


def sse_chunk(text: str) -> str:
    """把增量文本包装为 RTC 要求的 OpenAI 兼容 chunk 的 SSE data 行。"""
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": settings.ARK_ENDPOINT_ID,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _restore_state(session: dict) -> dict:
    """由 checkpointer 恢复图状态；全新会话写入初始状态。"""
    config = session_config(session["id"])
    graph = get_graph()
    state = await graph.aget_state(config)
    if not state.values:
        resume = (
            await storage.resume_get(session["user_id"], session["resume_id"])
            if session["resume_id"] else None
        )
        structured = {}
        if resume and resume.get("structured_json"):
            try:
                structured = json.loads(resume["structured_json"])
            except json.JSONDecodeError:
                structured = {}
        init = {
            "session_id": session["id"],
            "position": session["position"],
            "resume": structured,
            "stage": "intro",
            "round_no": 0,
            "stage_questions": [],
            "questions_asked": [],
            "current_question": None,
            "messages": [],
            "scores": [],
            "rag_context": "",
            "pending_user_text": "",
            "report": None,
            "prompt_versions": registry.snapshot_versions(),  # P7 §0.5：会话创建即固化提示词版本
        }
        await graph.aupdate_state(config, init)
        state = await graph.aget_state(config)
    return dict(state.values or {})


# ---------- 冷路径（Task 13） ----------
_cold_tasks: dict[str, asyncio.Task] = {}


async def await_pending_cold(session_id: str) -> None:
    """等待该会话未完成的冷任务（会话级串行化，防并发写图状态）。"""
    task = _cold_tasks.get(session_id)
    if task and not task.done():
        await task


async def run_cold_path(session_id: str) -> None:
    """回合结束后的异步冷路径：评分 → 出题/推进阶段 →（finish）报告落库。"""
    config = session_config(session_id)
    graph = get_graph()
    await graph.ainvoke(None, config)  # 从 interviewer 中断处恢复：evaluator → planner → 条件边
    state = dict((await graph.aget_state(config)).values or {})
    session = await storage.session_get_internal(session_id)
    if not session:
        return
    await storage.session_update(
        session["user_id"], session_id, {"stage": state.get("stage", session["stage"])},
    )
    report = state.get("report")
    if report:
        from services.report_service import save_report

        await save_report(session, report, state)
        await storage.session_update(session["user_id"], session_id, {
            "status": "finished", "stage": "finish", "ended_at": utc_now(),
        })


async def _serialized_cold(session_id: str, pending: asyncio.Task | None) -> None:
    try:
        if pending and not pending.done():
            await pending
        await run_cold_path(session_id)
    except Exception:
        logger.warning("Cold-path task failed session=%s", session_id)
    finally:
        # 身份守卫：只清理自己的条目，避免误删已被 newer 任务替换的项（R-T13-4）
        if _cold_tasks.get(session_id) is asyncio.current_task():
            _cold_tasks.pop(session_id, None)


def schedule_cold_path(session_id: str) -> None:
    """调度冷路径任务（fire-and-forget，绝不阻塞 SSE）。"""
    pending = _cold_tasks.pop(session_id, None)
    _cold_tasks[session_id] = asyncio.create_task(_serialized_cold(session_id, pending))


async def shutdown_cold_tasks() -> None:
    """应用关闭时取消并回收尚未完成的进程内冷任务。"""
    tasks = list(_cold_tasks.values())
    _cold_tasks.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
