"""interview API：/state 只读状态端点（Plan 3 T1，评分浮层/阶段指示数据源）。"""
import pytest
from fastapi import HTTPException

import api.interview as api


def _fake_session(**extra):
    s = {
        "id": "s1", "resume_id": None, "position": "Java后端", "stage": "intro",
        "status": "running", "started_at": "2026-01-01T00:00:00", "ended_at": None,
        "user_id": "u1",
    }
    s.update(extra)
    return s


def _ainvoke(result):
    async def f(*args, **kwargs):
        return result
    return f


class _FakeGraph:
    """aget_state 返回 values；None 模拟 checkpoint 丢失。"""

    def __init__(self, values):
        self.values = values

    async def aget_state(self, config):
        class Snap:
            pass
        snap = Snap()
        snap.values = self.values
        return snap


_SCORES = [{
    "dimensions": {
        "技术深度": {"score": 8, "reason": "ok"}, "项目理解": {"score": 7, "reason": "ok"},
        "表达沟通": {"score": 9, "reason": "ok"}, "临场表现": {"score": 6, "reason": "ok"},
    },
    "overall_score": 7.5, "strengths": [], "improvements": [], "comment": "c",
}]


def _mock(monkeypatch, session, values, report=None):
    monkeypatch.setattr(api.storage, "session_get", _ainvoke(session))
    monkeypatch.setattr(api.storage, "report_get_by_session", _ainvoke(report))
    fake_graph = _FakeGraph(values)
    monkeypatch.setattr(api, "get_graph", lambda: fake_graph)


async def test_state_returns_stage_and_scores(monkeypatch):
    _mock(monkeypatch, _fake_session(), {
        "session_id": "s1", "stage": "technical", "round_no": 3,
        "current_question": None, "scores": _SCORES,
    })
    result = await api.interview_state("s1", {"id": "u1"})
    assert result["session_id"] == "s1"
    assert result["stage"] == "technical"
    assert result["round_no"] == 3
    assert result["scores"] == _SCORES
    assert result["status"] == "running"
    assert result["report_id"] is None


async def test_state_404_when_session_missing(monkeypatch):
    monkeypatch.setattr(api.storage, "session_get", _ainvoke(None))
    with pytest.raises(HTTPException) as ei:
        await api.interview_state("ghost", {"id": "u1"})
    assert ei.value.status_code == 404


async def test_state_409_when_checkpoint_missing(monkeypatch):
    _mock(monkeypatch, _fake_session(), None)
    with pytest.raises(HTTPException) as ei:
        await api.interview_state("s1", {"id": "u1"})
    assert ei.value.status_code == 409


async def test_state_409_when_checkpoint_belongs_to_other_session(monkeypatch):
    # aupdate_state 对丢失 checkpoint 可能新建仅含 stage 的空状态：session_id 不匹配 → 409
    _mock(monkeypatch, _fake_session(), {"stage": "intro"})
    with pytest.raises(HTTPException) as ei:
        await api.interview_state("s1", {"id": "u1"})
    assert ei.value.status_code == 409


async def test_state_includes_report_id_when_finished(monkeypatch):
    _mock(
        monkeypatch,
        _fake_session(status="finished", stage="finish"),
        {"session_id": "s1", "stage": "finish", "round_no": 8, "current_question": None, "scores": []},
        report={"id": "r1", "session_id": "s1"},
    )
    result = await api.interview_state("s1", {"id": "u1"})
    assert result["status"] == "finished"
    assert result["stage"] == "finish"
    assert result["report_id"] == "r1"
