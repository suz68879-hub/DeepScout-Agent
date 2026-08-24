"""图流程集成：mock LLM/RAG 走通 intro→technical→deepdive→qa→finish 全状态机。"""
import pytest

# 执行；_patched_graph 内的 import 仅为 sys.modules 命中，不再触发模块体
import agents.graph  # noqa: F401


class FakeRetriever:
    async def aretrieve(self, query, top_k=3):
        return "参考上下文"


async def fake_evaluate_round(position, question, reference_points, answer, rag_context="", llm=None, prompt_versions=None):
    from agents.evaluator import DIMENSIONS
    return {
        "status": "ok",
        "dimensions": {d: {"score": 8, "reason": "r"} for d in DIMENSIONS},
        "overall_score": 8.0,
    }


async def fake_generate_question(state, rag_context="", llm=None):
    from agents.planner import Question
    n = len(state.get("questions_asked", [])) + 1
    return Question(
        question_text=f"{state['stage']}第{n}题", stage=state["stage"], topic="t",
        difficulty=1, reference_points=[], follow_up_hints=[], reason="r",
    )


async def fake_generate_report(state, llm=None):
    from agents.reporter import Report
    return Report(summary="测试报告", strengths=["s1", "s2"], improvements=["i1", "i2"], suggestions=["u1"])


async def _patched_graph(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "flow.db"))
    import agents.graph as gmod
    monkeypatch.setattr(gmod, "get_agent_llm", lambda name: object())
    monkeypatch.setattr(gmod, "get_retriever", lambda: FakeRetriever())
    monkeypatch.setattr(gmod, "evaluate_round", fake_evaluate_round)
    monkeypatch.setattr(gmod, "generate_question", fake_generate_question)
    monkeypatch.setattr(gmod, "generate_report", fake_generate_report)
    # R-T13-1：Task 7 的 build_graph 为 async（AsyncSqliteSaver），需 await
    return await gmod.build_graph()


async def _round(g, config, user_text):
    """一轮：hot（写用户消息 → invoke 中断于 interviewer）→ 写助手消息 → cold resume。"""
    await g.aupdate_state(config, {
        "messages": [{"role": "user", "content": user_text}],
        "pending_user_text": user_text,
    }, as_node="__start__")
    await g.ainvoke(None, config)
    await g.aupdate_state(config, {"messages": [{"role": "assistant", "content": "好的"}]}, as_node="interviewer")
    await g.ainvoke(None, config)
    return dict((await g.aget_state(config)).values or {})


async def test_full_flow_stage_progression(tmp_path, monkeypatch):
    g = await _patched_graph(tmp_path, monkeypatch)
    config = {"configurable": {"thread_id": "s-flow"}}
    await g.aupdate_state(config, {
        "session_id": "s-flow", "position": "Java后端", "resume": {},
        "stage": "intro", "round_no": 0, "stage_questions": [],
        "questions_asked": [],
        "current_question": None, "messages": [], "scores": [],
        "rag_context": "", "pending_user_text": "", "report": None,
    }, as_node="__start__")
    # 热路径单跑：不触发冷节点（scores 仍为空）
    await g.aupdate_state(config, {"messages": [{"role": "user", "content": "大家好"}]}, as_node="__start__")
    await g.ainvoke(None, config)
    hot = dict((await g.aget_state(config)).values or {})
    assert hot["scores"] == []

    # intro 1 轮 → technical
    state = await _round(g, config, "我是一年经验 Java 后端")
    assert state["stage"] == "technical"
    assert state["current_question"]["question_text"].startswith("technical")
    assert len(state["scores"]) == 1  # intro 轮照常评分

    # technical：6 题后进 deepdive
    for i in range(6):
        state = await _round(g, config, f"technical 回答{i}")
    assert state["stage"] == "deepdive"
    assert state["current_question"]["question_text"].startswith("deepdive")

    # deepdive：空简历按 1 项目 → 3 轮后进 qa
    for i in range(3):
        state = await _round(g, config, f"deepdive 回答{i}")
    assert state["stage"] == "qa"
    assert state["current_question"] is None

    # qa：2 轮后 finish → reporter 已运行（report 落状态；qa 轮不评分）
    for i in range(2):
        state = await _round(g, config, f"候选人反问{i}")
    assert state["stage"] == "finish"
    assert state["report"] is not None and state["report"]["summary"] == "测试报告"
    assert len(state["scores"]) == 1 + 6 + 3  # intro 1 + technical 6 + deepdive 3


async def test_hot_invoke_does_not_trigger_cold_nodes(tmp_path, monkeypatch):
    g = await _patched_graph(tmp_path, monkeypatch)
    config = {"configurable": {"thread_id": "s-hot"}}
    await g.aupdate_state(config, {
        "session_id": "s-hot", "position": "Java后端", "resume": {},
        "stage": "deepdive", "round_no": 0, "stage_questions": [],
        "questions_asked": [],
        "current_question": None, "messages": [], "scores": [],
        "rag_context": "", "pending_user_text": "", "report": None,
    }, as_node="__start__")
    await g.aupdate_state(config, {"messages": [{"role": "user", "content": "回答"}]}, as_node="__start__")
    await g.ainvoke(None, config)
    state = dict((await g.aget_state(config)).values or {})
    assert state["scores"] == [] and state["round_no"] == 0
