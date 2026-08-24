"""interview API：/finish 幂等守卫与 checkpoint 空状态守卫（终审 R-FINAL-1 两项）。"""
import pytest
from fastapi import HTTPException

import api.interview as api


def _fake_session(status="running", **extra):
    s = {
        "id": "s1", "resume_id": None, "position": "Java后端", "stage": "intro",
        "status": status, "started_at": "2026-01-01T00:00:00", "ended_at": None,
        "user_id": "u1",
    }
    s.update(extra)
    return s


def _ainvoke(result):
    async def f(*args, **kwargs):
        return result
    return f


class _FakeGraph:
    """aget_state 返回 values；None 模拟 checkpoint 丢失；记录 aupdate_state 调用。"""

    def __init__(self, values):
        self.values = values
        self.updates = []

    async def aupdate_state(self, config, values, **kwargs):
        self.updates.append((config, values, kwargs))

    async def aget_state(self, config):
        class Snap:
            pass
        snap = Snap()
        snap.values = self.values
        return snap


_FAKE_REPORT = {
    "overall_score": 6.0, "dimension_scores": {}, "summary": "", "round_details": [],
    "strengths": [], "improvements": [], "suggestions": [],
}


class _FakeReportModel:
    def model_dump(self):
        return _FAKE_REPORT


def _mock_finish_chain(monkeypatch, session):
    """mock /finish 正常链路：缺少守卫时能走通 200（RED 语义清晰）。"""
    monkeypatch.setattr(api.storage, "session_get", _ainvoke(session))
    monkeypatch.setattr(api.storage, "session_update", _ainvoke({}))
    fake_graph = _FakeGraph(
        {"session_id": session["id"], "position": session["position"], "messages": []}
    )
    monkeypatch.setattr(api, "get_graph", lambda: fake_graph)
    monkeypatch.setattr(api, "generate_report", _ainvoke(_FakeReportModel()))
    monkeypatch.setattr(api, "save_report", _ainvoke("r1"))
    monkeypatch.setattr(api, "get_agent_llm", lambda agent: "fake-llm")


async def test_finish_returns_report_id_on_normal_path(monkeypatch):
    _mock_finish_chain(monkeypatch, _fake_session())
    result = await api.finish_interview(api.FinishRequest(session_id="s1"), {"id": "u1"})
    assert result == {"session_id": "s1", "report_id": "r1", "status": "finished"}


async def test_finish_rejects_duplicate_when_already_finished(monkeypatch):
    # 幂等守卫：双击 /finish 会重复生成报告行；已结束会话直接 409
    _mock_finish_chain(monkeypatch, _fake_session(status="finished"))
    with pytest.raises(HTTPException) as ei:
        await api.finish_interview(api.FinishRequest(session_id="s1"), {"id": "u1"})
    assert ei.value.status_code == 409


async def test_finish_rejects_when_checkpoint_state_missing(monkeypatch):
    # checkpoint 丢失：aget_state values 为 None → 静默全零报告；必须 409
    _mock_finish_chain(monkeypatch, _fake_session())
    monkeypatch.setattr(api, "get_graph", lambda: _FakeGraph(None))
    with pytest.raises(HTTPException) as ei:
        await api.finish_interview(api.FinishRequest(session_id="s1"), {"id": "u1"})
    assert ei.value.status_code == 409


async def test_finish_rejects_when_checkpoint_belongs_to_other_session(monkeypatch):
    # aupdate_state 对丢失 checkpoint 可能新建仅含 stage 的空状态：session_id 不匹配 → 409
    _mock_finish_chain(monkeypatch, _fake_session())
    monkeypatch.setattr(api, "get_graph", lambda: _FakeGraph({"stage": "finish"}))
    with pytest.raises(HTTPException) as ei:
        await api.finish_interview(api.FinishRequest(session_id="s1"), {"id": "u1"})
    assert ei.value.status_code == 409


async def test_finish_updates_stage_with_as_node_planner(monkeypatch):
    # 回归（T11 验收发现）：共享状态图缺省 as_node 会报 InvalidUpdateError:
    # Ambiguous update；stage 归属 planner 节点（graph.py 条件边按 finish 路由 reporter）
    fake = _FakeGraph({"session_id": "s1", "position": "Java后端", "messages": []})
    _mock_finish_chain(monkeypatch, _fake_session())
    monkeypatch.setattr(api, "get_graph", lambda: fake)
    await api.finish_interview(api.FinishRequest(session_id="s1"), {"id": "u1"})
    assert len(fake.updates) == 1
    assert fake.updates[0][1] == {"stage": "finish"}
    assert fake.updates[0][2] == {"as_node": "planner"}
