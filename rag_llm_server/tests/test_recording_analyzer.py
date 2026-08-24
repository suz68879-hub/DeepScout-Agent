"""P6 T5：录音分析 Agent 单测——角色判定 / 报告生成 / 校验重试 / 启发式兜底（LLM 全部打桩）。"""
import pytest

from agents.recording_analyzer import (
    SpeakerAssignment,
    _fallback_assignment,
    candidate_segments,
    generate_recording_report,
    judge_roles,
    label_roles,
)


class _FakeStructured:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._results:
            raise RuntimeError("no more results")
        r = self._results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _FakeLlm:
    def __init__(self, results):
        self._structured = _FakeStructured(results)

    def with_structured_output(self, _schema):
        return self._structured


TRANSCRIPT = [
    {"speaker": "0", "start_ms": 0, "end_ms": 1000, "text": "请做自我介绍"},
    {"speaker": "1", "start_ms": 1000, "end_ms": 9000, "text": "我是张三，有三年后端经验。" * 3},
]


async def test_judge_roles_returns_llm_assignment():
    llm = _FakeLlm([SpeakerAssignment(candidate_speaker="1", confidence="高", reason="提问短回答长")])
    a = await judge_roles(TRANSCRIPT, llm=llm)
    assert a.candidate_speaker == "1"
    assert a.confidence == "高"
    assert "请做自我介绍" in llm._structured.calls[0][0].content


async def test_judge_roles_falls_back_to_heuristic_on_llm_error():
    llm = _FakeLlm([RuntimeError("结构化输出失败")])
    a = await judge_roles(TRANSCRIPT, llm=llm)
    assert a.candidate_speaker == "1"
    assert a.confidence == "低"


async def test_fallback_assignment_picks_longest_speech():
    a = _fallback_assignment(TRANSCRIPT)
    assert a.candidate_speaker == "1"
    assert a.confidence == "低"


async def test_generate_recording_report_retries_once_then_succeeds():
    from agents.reporter import Report

    invalid = Report(summary="", strengths=["s"], improvements=["i"], suggestions=["u"])
    valid = Report(
        summary="整体不错",
        dimension_scores={"技术深度": 6, "项目理解": 7, "表达沟通": 6, "临场表现": 7},
        overall_score=7.0,
        round_details=[], strengths=["s1", "s2"], improvements=["i1", "i2"], suggestions=["u1"],
    )
    llm = _FakeLlm([invalid, valid])
    r = await generate_recording_report(
        [{"speaker": "1", "start_ms": 0, "end_ms": 100, "text": "回答"}], "Java后端", llm=llm
    )
    assert r.summary == "整体不错"
    assert len(llm._structured.calls) == 2


async def test_generate_recording_report_raises_after_two_failures():
    from agents.reporter import Report

    invalid = Report(summary="", strengths=[], improvements=[], suggestions=[])
    llm = _FakeLlm([invalid, invalid])
    with pytest.raises(RuntimeError):
        await generate_recording_report(
            [{"speaker": "1", "start_ms": 0, "end_ms": 100, "text": "回答"}], "Java后端", llm=llm
        )


async def test_generate_recording_report_raises_on_missing_dimension_scores():
    from agents.reporter import Report

    missing_dims = Report(
        summary="整体不错", dimension_scores={}, overall_score=7.0,
        round_details=[], strengths=["s1", "s2"], improvements=["i1", "i2"], suggestions=["u1"],
    )
    llm = _FakeLlm([missing_dims, missing_dims])
    with pytest.raises(RuntimeError):
        await generate_recording_report(
            [{"speaker": "1", "start_ms": 0, "end_ms": 100, "text": "回答"}], "Java后端", llm=llm
        )


async def test_label_roles_and_candidate_segments():
    a = SpeakerAssignment(candidate_speaker="1", confidence="高", reason="")
    labeled = label_roles(TRANSCRIPT, a)
    assert [s["role"] for s in labeled] == ["面试官", "候选人"]
    segs = candidate_segments(TRANSCRIPT, a)
    assert [s["text"] for s in segs] == [TRANSCRIPT[1]["text"]]


async def test_recording_analyzer_templates_registered_and_renderable():
    from agents.prompts.registry import registry

    t1 = registry.get("recording_analyzer", "system")
    assert t1.render(position="Java后端", transcript_brief="[1] x", output_schema="{}")
    t2 = registry.get("recording_analyzer", "role_judge")
    assert t2.render(transcript_brief="[1] x", output_schema="{}")
