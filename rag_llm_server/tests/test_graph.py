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
    for _ in range(3):
        snapshot = await g.aget_state(config)
        if not snapshot.next:
            break
        await g.ainvoke(None, config)
    return dict((await g.aget_state(config)).values or {})


async def test_evaluator_node_skips_intro_scoring(monkeypatch):
    import agents.graph as graph_module

    async def unexpected_evaluate(**_kwargs):
        raise AssertionError("intro 轮不得调用 evaluate_round")

    monkeypatch.setattr(graph_module, "evaluate_round", unexpected_evaluate)
    monkeypatch.setattr(graph_module, "get_retriever", lambda: FakeRetriever())
    monkeypatch.setattr(graph_module, "get_agent_llm", lambda _name: object())

    result = await graph_module.evaluator_node({
        "stage": "intro",
        "round_no": 0,
        "position": "Java后端",
        "messages": [{"role": "user", "content": "我是一年经验 Java 后端"}],
        "scores": [],
        "current_question": None,
    })
    assert result["scores"] == []
    assert result["round_no"] == 1


@pytest.mark.parametrize("stage", ("qa", "finish"))
async def test_evaluator_node_skips_unscored_stages(monkeypatch, stage):
    import agents.graph as graph_module

    async def unexpected_evaluate(**_kwargs):
        raise AssertionError(f"{stage} 轮不得调用 evaluate_round")

    monkeypatch.setattr(graph_module, "evaluate_round", unexpected_evaluate)
    result = await graph_module.evaluator_node({
        "stage": stage,
        "round_no": 0,
        "messages": [{"role": "user", "content": "我想问加班多吗"}],
        "scores": [{"status": "ok"}],
        "current_question": None,
    })
    assert result["scores"] == [{"status": "ok"}]
    assert result["round_no"] == 1


async def test_evaluator_node_skips_pending_ask_without_increment(monkeypatch):
    import agents.graph as graph_module

    async def unexpected_evaluate(**_kwargs):
        raise AssertionError("pending_ask 轮不得评分")

    monkeypatch.setattr(graph_module, "evaluate_round", unexpected_evaluate)
    result = await graph_module.evaluator_node({
        "stage": "technical",
        "round_no": 0,
        "pending_ask": True,
        "messages": [{"role": "user", "content": "好的请开始"}],
        "scores": [],
        "current_question": {"question_text": "讲讲 JVM"},
    })
    assert result["scores"] == []
    assert result["round_no"] == 0


async def test_evaluator_rejects_non_retryable_model_output(monkeypatch):
    import agents.graph as graph_module

    async def failed_evaluation(**_kwargs):
        return {"status": "failed"}

    monkeypatch.setattr(graph_module, "evaluate_round", failed_evaluation)
    monkeypatch.setattr(graph_module, "get_retriever", lambda: FakeRetriever())
    monkeypatch.setattr(graph_module, "get_agent_llm", lambda _name: object())

    with pytest.raises(graph_module.ColdPathOutputError, match="EVALUATOR_OUTPUT_INVALID"):
        await graph_module.evaluator_node({
            "stage": "technical",
            "round_no": 0,
            "position": "Java后端",
            "messages": [{"role": "user", "content": "回答"}],
            "scores": [],
            "current_question": None,
        })


async def test_full_flow_stage_progression(tmp_path, monkeypatch):
    g = await _patched_graph(tmp_path, monkeypatch)
    try:
        config = {"configurable": {"thread_id": "s-flow"}}
        await g.aupdate_state(config, {
            "session_id": "s-flow", "position": "Java后端", "resume": {},
            "stage": "intro", "round_no": 0, "stage_questions": [],
            "questions_asked": [],
            "current_question": None, "messages": [], "scores": [],
            "rag_context": "", "pending_user_text": "", "report": None,
            "pending_ask": False,
        }, as_node="__start__")
        # 热路径单跑：不触发冷节点（scores 仍为空）
        await g.aupdate_state(config, {"messages": [{"role": "user", "content": "大家好"}]}, as_node="__start__")
        await g.ainvoke(None, config)
        hot = dict((await g.aget_state(config)).values or {})
        assert hot["scores"] == []

        # intro 1 轮：不评分；planner 进入 technical 并出第一题，下一轮才宣读
        state = await _round(g, config, "我是一年经验 Java 后端")
        assert state["stage"] == "technical"
        assert state["current_question"]["question_text"].startswith("technical")
        assert state["scores"] == []
        assert state["pending_ask"] is True

        state = await _round(g, config, "好的，请开始")
        assert state["scores"] == []
        assert state["pending_ask"] is False
        assert state["stage"] == "technical"

        # technical：6 题后进 deepdive；先宣读深挖第一题再作答
        for i in range(6):
            state = await _round(g, config, f"technical 回答{i}")
        assert state["stage"] == "deepdive"
        assert state["current_question"]["question_text"].startswith("deepdive")
        assert state["pending_ask"] is True
        state = await _round(g, config, "好的")
        assert state["pending_ask"] is False

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
        assert len(state["scores"]) == 6 + 3  # technical 6 + deepdive 3；intro/qa 不评分
    finally:
        import agents.graph as graph_module
        await graph_module.close_graph()


async def test_hot_invoke_does_not_trigger_cold_nodes(tmp_path, monkeypatch):
    g = await _patched_graph(tmp_path, monkeypatch)
    try:
        config = {"configurable": {"thread_id": "s-hot"}}
        await g.aupdate_state(config, {
            "session_id": "s-hot", "position": "Java后端", "resume": {},
            "stage": "deepdive", "round_no": 0, "stage_questions": [],
            "questions_asked": [],
            "current_question": None, "messages": [], "scores": [],
            "rag_context": "", "pending_user_text": "", "report": None,
            "pending_ask": False,
        }, as_node="__start__")
        await g.aupdate_state(config, {"messages": [{"role": "user", "content": "回答"}]}, as_node="__start__")
        await g.ainvoke(None, config)
        state = dict((await g.aget_state(config)).values or {})
        assert state["scores"] == [] and state["round_no"] == 0
    finally:
        import agents.graph as graph_module
        await graph_module.close_graph()
