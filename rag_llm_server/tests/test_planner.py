"""Planner：结构化输出、few-shot 注入、重试与兜底题库。"""
import pytest

from agents.planner import FALLBACK_QUESTIONS, Question, _fallback_question, generate_question


class FakeStructured:
    def __init__(self, results):
        self.results = list(results)
        self.messages_seen = []

    async def ainvoke(self, msgs):
        self.messages_seen.append(msgs)
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeLLM:
    def __init__(self, results):
        self.structured = FakeStructured(results)

    def with_structured_output(self, schema):
        return self.structured


def _state(**over):
    s = {
        "position": "Java后端", "stage": "technical", "resume": {"projects": [{"name": "秒杀"}]},
        "questions_asked": [], "current_question": None, "messages": [],
    }
    s.update(over)
    return s


def _q(text, topic="JVM"):
    return Question(
        question_text=text, stage="technical", topic=topic, difficulty=1,
        reference_points=[], follow_up_hints=[], reason="r",
    )


def test_resume_json_is_wrapped_as_untrusted_data():
    llm = FakeLLM([_q("讲讲 JVM")])

    import asyncio
    asyncio.run(generate_question(_state(), "参考：JVM 分区", llm))
    sent = llm.structured.messages_seen[0][0].content
    assert '<untrusted_data source="resume">' in sent
    assert "秒杀" in sent
    assert "候选人提供的数据" in sent


def test_generate_question_success_path():
    llm = FakeLLM([_q("讲讲 JVM")])

    import asyncio
    q = asyncio.run(generate_question(_state(), "参考：JVM 分区", llm))
    assert q.question_text == "讲讲 JVM"
    sent = llm.structured.messages_seen[0][0].content
    assert "出题示例（Few-shot）" in sent  # R4：示例注入
    assert "参考：JVM 分区" in sent      # RAG 上下文注入
    assert '"question_text"' in sent    # P7：output_schema 经统一注入点渲染


def test_generate_question_duplicate_triggers_retry_with_feedback():
    llm = FakeLLM([_q("重复题"), _q("新题")])

    import asyncio
    q = asyncio.run(generate_question(_state(questions_asked=[{"question_text": "重复题"}]), "", llm))
    assert q.question_text == "新题"
    assert len(llm.structured.messages_seen) == 2
    assert "校验失败" in llm.structured.messages_seen[1][0].content


def test_generate_question_falls_back_after_two_failures():
    llm = FakeLLM([ValueError("解析失败"), ValueError("解析失败")])

    import asyncio
    q = asyncio.run(generate_question(_state(), "", llm))
    assert "秒杀" in q.question_text
    assert q.reason.startswith("兜底题库")


def test_fallback_skips_asked_questions():
    asked = [FALLBACK_QUESTIONS["technical"][0]["question_text"]]
    llm = FakeLLM([ValueError("x"), ValueError("x")])

    import asyncio
    q = asyncio.run(generate_question(_state(questions_asked=[{"question_text": a} for a in asked]), "", llm))
    assert q.question_text not in asked


def test_fallback_pool_sizes():
    assert len(FALLBACK_QUESTIONS["deepdive"]) == 3
    assert len(FALLBACK_QUESTIONS["technical"]) == 3


def test_technical_fallback_is_anchored_to_resume_skill():
    q = _fallback_question(
        "technical",
        [],
        {"skills": [{"name": "Redis"}], "projects": [{"name": "秒杀系统"}]},
    )
    assert "Redis" in q.question_text
    assert "Java 内存模型" not in q.question_text
    assert q.topic == "Redis"


def test_deepdive_fallback_names_resume_project():
    q = _fallback_question(
        "deepdive",
        [],
        {"skills": [{"name": "Java"}], "projects": [{"name": "秒杀系统"}]},
    )
    assert "秒杀系统" in q.question_text
