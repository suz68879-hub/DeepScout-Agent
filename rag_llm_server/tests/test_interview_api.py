"""interview API：/finish 幂等守卫与 checkpoint 空状态守卫（终审 R-FINAL-1 两项）。"""
import pytest
from fastapi import HTTPException

import api.interview as api
from services.jobs.types import JobStatus


def _fake_session(status="running", **extra):
    s = {
        "id": "s1", "resume_id": None, "position": "Java后端", "stage": "intro",
        "status": status, "started_at": "2026-01-01T00:00:00", "ended_at": None,
        "user_id": "u1", "version": 1,
    }
    s.update(extra)
    return s


def _ainvoke(result):
    async def f(*args, **kwargs):
        return result
    return f


class _JsonRequest:
    def __init__(self, session_id: str):
        self.headers = {"content-type": "application/json"}
        self._session_id = session_id

    async def json(self):
        return {"session_id": self._session_id}


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
    monkeypatch.setattr(api.settings, "ENABLE_LEGACY_SYNC_FINISH", True, raising=False)


async def test_finish_returns_202_durable_job_contract(monkeypatch):
    session = _fake_session()
    monkeypatch.setattr(api.settings, "ENABLE_LEGACY_SYNC_FINISH", False, raising=False)
    monkeypatch.setattr(api.storage, "session_get", _ainvoke(session))
    fake_graph = _FakeGraph(
        {"session_id": session["id"], "position": session["position"], "messages": []}
    )
    monkeypatch.setattr(api, "get_graph", lambda: fake_graph)

    class Job:
        id = "33333333-3333-3333-3333-333333333333"
        status = JobStatus.PENDING

    async def schedule(session_id, owner_id):
        assert (session_id, owner_id) == ("s1", "u1")
        return Job()

    monkeypatch.setattr(api, "schedule_finish_job", schedule, raising=False)
    finishing = []

    async def session_update(_user_id, session_id, patch, expected_version=None):
        finishing.append((session_id, patch, expected_version))
        return {**session, **patch, "version": 2}

    monkeypatch.setattr(api.storage, "session_update", session_update)

    result = await api.finish_interview(
        api.FinishRequest(session_id="s1"), {"id": "u1"}
    )

    assert result == {
        "job_id": "33333333-3333-3333-3333-333333333333",
        "session_id": "s1",
        "status": "pending",
    }
    assert finishing == [("s1", {"status": "finishing"}, 1)]
    assert fake_graph.updates[-1][1:] == ({"stage": "finish"}, {"as_node": "planner"})


@pytest.mark.parametrize(
    "job_status",
    [JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED],
)
async def test_finish_returns_actual_job_status(monkeypatch, job_status):
    session = _fake_session(status="finishing")
    monkeypatch.setattr(api.settings, "ENABLE_LEGACY_SYNC_FINISH", False, raising=False)
    monkeypatch.setattr(api.storage, "session_get", _ainvoke(session))
    fake_graph = _FakeGraph(
        {"session_id": session["id"], "position": session["position"], "messages": []}
    )
    monkeypatch.setattr(api, "get_graph", lambda: fake_graph)

    class Job:
        id = "44444444-4444-4444-4444-444444444444"
        status = job_status

    async def schedule(session_id, owner_id):
        assert (session_id, owner_id) == ("s1", "u1")
        return Job()

    monkeypatch.setattr(api, "schedule_finish_job", schedule, raising=False)

    result = await api.finish_interview(
        api.FinishRequest(session_id="s1"), {"id": "u1"}
    )

    assert result == {
        "job_id": "44444444-4444-4444-4444-444444444444",
        "session_id": "s1",
        "status": job_status.value,
    }


def test_finish_route_declares_202_accepted():
    route = next(
        route for route in api.router.routes if getattr(route, "path", "") == "/api/interview/finish"
    )
    assert route.status_code == 202


async def test_start_scopes_idempotency_to_validated_body(monkeypatch):
    captured = {}

    async def session_create(_user_id, row):
        return {**row, "id": "s1"}

    async def fake_execute(request, user, body, operation):
        captured.update({"request": request, "user": user, "body": body})
        return await operation()

    monkeypatch.setattr(api.storage, "session_list_running", _ainvoke([]))
    monkeypatch.setattr(api.storage, "session_create", session_create)
    monkeypatch.setattr(api, "get_graph", lambda: _FakeGraph({}))
    monkeypatch.setattr(api, "execute_idempotent", fake_execute, raising=False)
    request = object()
    result = await api.start_interview(
        api.StartRequest(position="Backend"), {"id": "u1"}, request
    )
    assert result == {"session_id": "s1", "position": "Backend", "stage": "intro"}
    assert captured == {
        "request": request,
        "user": {"id": "u1"},
        "body": {"position": "Backend", "resume_id": None},
    }


async def test_finish_returns_report_id_on_normal_path(monkeypatch):
    _mock_finish_chain(monkeypatch, _fake_session())
    result = await api.finish_interview(api.FinishRequest(session_id="s1"), {"id": "u1"})
    assert result == {"session_id": "s1", "report_id": "r1", "status": "finished"}


async def test_finish_scopes_idempotency_to_validated_body(monkeypatch):
    _mock_finish_chain(monkeypatch, _fake_session())
    captured = {}

    async def fake_execute(request, user, body, operation):
        captured.update({"request": request, "user": user, "body": body})
        return await operation()

    monkeypatch.setattr(api, "execute_idempotent", fake_execute)
    request = object()
    result = await api.finish_interview(
        api.FinishRequest(session_id="s1"), {"id": "u1"}, request
    )
    assert result["report_id"] == "r1"
    assert captured == {
        "request": request,
        "user": {"id": "u1"},
        "body": {"session_id": "s1"},
    }


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


async def test_start_reclaims_existing_running_then_creates(monkeypatch):
    running = [_fake_session(id="old")]
    updates = []

    async def list_running(_user_id):
        return list(running)

    async def session_update(_user_id, session_id, patch, expected_version=None):
        updates.append((session_id, patch))
        running.clear()
        return {**_fake_session(id=session_id), **patch}

    async def session_create(_user_id, row):
        return {**row, "id": "s-new"}

    monkeypatch.setattr(api.storage, "session_list_running", list_running)
    monkeypatch.setattr(api.storage, "session_get", _ainvoke(_fake_session(id="old")))
    monkeypatch.setattr(api.storage, "session_update", session_update)
    monkeypatch.setattr(api.storage, "session_create", session_create)
    monkeypatch.setattr(api, "get_graph", lambda: _FakeGraph({}))

    result = await api.start_interview(
        api.StartRequest(position="Backend"), {"id": "u1"}
    )
    assert result == {"session_id": "s-new", "position": "Backend", "stage": "intro"}
    assert updates == [("old", {"status": "abandoned", "ended_at": updates[0][1]["ended_at"]})]
    assert updates[0][1]["status"] == "abandoned"
    assert updates[0][1]["ended_at"]


async def test_abandon_marks_running_session_without_report(monkeypatch):
    updates = []

    async def session_get(_user_id, session_id):
        return _fake_session(id=session_id)

    async def session_update(_user_id, session_id, patch, expected_version=None):
        updates.append(patch)
        return {**_fake_session(id=session_id), **patch}

    monkeypatch.setattr(api.storage, "session_get", session_get)
    monkeypatch.setattr(api.storage, "session_update", session_update)
    result = await api.abandon_interview(_JsonRequest("s1"), {"id": "u1"})
    assert result["session_id"] == "s1"
    assert result["status"] == "abandoned"
    assert updates[0]["status"] == "abandoned"
    assert updates[0]["ended_at"]


async def test_abandon_is_noop_when_already_finished(monkeypatch):
    updates = []
    monkeypatch.setattr(
        api.storage, "session_get", _ainvoke(_fake_session(status="finished"))
    )

    async def session_update(*_args, **_kwargs):
        updates.append(True)
        raise AssertionError("finished session must not be patched")

    monkeypatch.setattr(api.storage, "session_update", session_update)
    result = await api.abandon_interview(_JsonRequest("s1"), {"id": "u1"})
    assert result["status"] == "finished"
    assert updates == []


async def test_abandon_404_when_session_missing(monkeypatch):
    monkeypatch.setattr(api.storage, "session_get", _ainvoke(None))
    with pytest.raises(HTTPException) as ei:
        await api.abandon_interview(_JsonRequest("ghost"), {"id": "u1"})
    assert ei.value.status_code == 404
