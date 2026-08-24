"""Reporter：代码均值、校验重试与兜底报告。"""
import pytest

from agents.reporter import DIMENSIONS, Report, compute_average_scores, generate_report


def _scores():
    base = {d: {"score": s, "reason": "r"} for d, s in zip(DIMENSIONS, [6, 8, 7, 9])}
    plus1 = {d: {"score": s + 1, "reason": "r"} for d, s in zip(DIMENSIONS, [6, 8, 7, 9])}
    return [
        {"status": "ok", "dimensions": base, "overall_score": 7.5},
        {"status": "failed"},
        {"status": "ok", "dimensions": plus1, "overall_score": 8.5},
    ]


def _state():
    return {
        "session_id": "s1", "position": "Java后端",
        "resume": {"skills": [{"name": "Java"}], "projects": []},
        "scores": _scores(),
        "messages": [
            {"role": "user", "content": "自我介绍"},
            {"role": "assistant", "content": "你好"},
        ],
    }


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


def _report(**over):
    data = {
        "summary": "表现良好",
        "dimension_scores": {d: 1.0 for d in DIMENSIONS},
        "overall_score": 1.0,
        "round_details": [],
        "strengths": ["s1", "s2"],
        "improvements": ["i1", "i2"],
        "suggestions": ["u1"],
    }
    data.update(over)
    return Report(**data)


def test_compute_average_scores_excludes_failed():
    dims, overall = compute_average_scores(_scores())
    assert dims["技术深度"] == 6.5  # (6+7)/2
    assert dims["临场表现"] == 9.5  # (9+10)/2
    assert overall == 8.0            # (6.5+8.5+7.5+9.5)/4


def test_compute_average_scores_empty_returns_zeros():
    dims, overall = compute_average_scores([])
    assert overall == 0.0 and set(dims) == set(DIMENSIONS)


def test_generate_report_overrides_llm_scores():
    llm = FakeLLM([_report()])
    import asyncio
    r = asyncio.run(generate_report(_state(), llm))
    assert r.overall_score == 8.0                     # 代码均值，非 LLM 给的 1.0
    assert r.dimension_scores["技术深度"] == 6.5
    assert r.summary == "表现良好"                     # 评语保留 LLM 输出
    assert r.strengths == ["s1", "s2"]


def test_generate_report_fallback_on_double_failure():
    llm = FakeLLM([RuntimeError("boom"), RuntimeError("boom")])
    import asyncio
    r = asyncio.run(generate_report(_state(), llm))
    assert "自动汇总" in r.summary
    assert r.overall_score == 8.0                     # 兜底报告也带代码均值
    assert len(llm.structured.messages_seen) == 2


def test_generate_report_retries_on_invalid_structure():
    llm = FakeLLM([_report(strengths=["仅一条"]), _report()])
    import asyncio
    r = asyncio.run(generate_report(_state(), llm))
    assert len(llm.structured.messages_seen) == 2
    assert r.strengths == ["s1", "s2"]
