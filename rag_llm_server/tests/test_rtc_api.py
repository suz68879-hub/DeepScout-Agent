"""RTC router compatibility tests for explicit interview sessions."""
import json
from contextlib import asynccontextmanager

import pytest

import api.rtc as rtc_api
import services.interview_service as isv
from agents.interviewer import ERROR_REPLY
from services.distributed_lock import LockBusy, LockLost
from services.interview_service import ColdPathStateError
from services.redis_client import SharedStateUnavailable
from services.rtc_service import RTC_LOCK_WAIT_SECONDS


USER = {"id": "u1", "username": "alice"}
SESSION = {
    "id": "s1",
    "user_id": "u1",
    "status": "running",
    "rtc_room_id": "room1",
    "rtc_user_id": "rtc-user1",
    "rtc_task_id": "task1",
    "rtc_callback_id": "callback1",
    "rtc_status": "created",
}


class Request:
    def __init__(self, body, **query_params):
        self._body = body
        self.query_params = query_params

    async def json(self):
        return self._body


async def async_value(value):
    return value


async def test_get_scenes_preserves_response_and_adds_session(monkeypatch):
    monkeypatch.setattr(rtc_api.storage, "session_get", lambda *args: async_value(SESSION))
    expected = {
        "ResponseMetadata": {"Action": "getScenes"},
        "Result": {"scenes": []},
        "SessionId": "s1",
    }
    monkeypatch.setattr(rtc_api, "get_scenes_payload", lambda session: expected)
    result = await rtc_api.get_scenes(rtc_api.SceneRequest(SessionId="s1"), USER)
    assert result == expected


async def test_proxy_uses_server_side_session_identifiers(monkeypatch):
    calls = []
    monkeypatch.setattr(rtc_api.storage, "session_get", lambda *args: async_value(SESSION))

    async def fake_call(action, version, session, body):
        calls.append((action, version, session, body))
        return {"ok": True}

    monkeypatch.setattr(rtc_api, "call_voice_chat_openapi", fake_call)
    request = Request({}, Action="StopVoiceChat", Version="v1")
    body = rtc_api.ProxyRequest(SessionId="s1", SceneID="scene")
    assert await rtc_api.proxy(request, body, USER) == {"ok": True}
    assert calls == [("StopVoiceChat", "v1", SESSION, {"SessionId": "s1", "SceneID": "scene"})]


@pytest.mark.parametrize("action", ["ListApps", "StartRecord", "", None])
async def test_proxy_rejects_unknown_action_before_provider_call(monkeypatch, action):
    calls = []
    monkeypatch.setattr(rtc_api.storage, "session_get", lambda *args: async_value(SESSION))

    async def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("unknown Action must not reach OpenAPI")

    monkeypatch.setattr(rtc_api, "call_voice_chat_openapi", forbidden)
    query = {} if action is None else {"Action": action}
    with pytest.raises(rtc_api.HTTPException) as exc_info:
        await rtc_api.proxy(
            Request({}, **query),
            rtc_api.ProxyRequest(SessionId="s1"),
            USER,
        )
    assert exc_info.value.status_code == 400
    assert "action" in str(exc_info.value.detail).lower()
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (LockBusy("busy"), 409, "RTC session is busy; retry the request"),
        (LockLost("lost"), 409, "RTC session lock was lost; retry the request"),
        (SharedStateUnavailable("unavailable"), 503, "shared state unavailable"),
    ],
)
async def test_proxy_maps_shared_lock_failures_without_leaking_details(
    monkeypatch, error, status_code, detail,
):
    monkeypatch.setattr(rtc_api.storage, "session_get", lambda *args: async_value(SESSION))

    async def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(rtc_api, "call_voice_chat_openapi", fail)
    with pytest.raises(rtc_api.HTTPException) as exc_info:
        await rtc_api.proxy(
            Request({}, Action="StartVoiceChat"),
            rtc_api.ProxyRequest(SessionId="s1"),
            USER,
        )
    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == detail
    if status_code == 409:
        assert exc_info.value.headers["Retry-After"] in {"1", "2"}


async def test_callback_rejects_invalid_signature_when_secret_is_configured(monkeypatch):
    monkeypatch.setattr(rtc_api.settings, "RTC_CALLBACK_SECRET", "secret")
    response = await rtc_api.chat_callback(
        Request({"Signature": "invalid"}), "callback1",
    )
    assert response.status_code == 403
    assert json.loads(response.body) == {"text": "signature verification failed"}


async def test_callback_rejects_tampered_messages_when_signature_was_valid(monkeypatch):
    from services.callback_verify import compute_signature

    monkeypatch.setattr(rtc_api.settings, "RTC_CALLBACK_SECRET", "secret")
    body = {
        "AppId": "app1",
        "EventId": "evt-tamper",
        "EventTime": "1700000000",
        "Nonce": "n1",
        "messages": [{"role": "user", "content": "hello"}],
    }
    body["Signature"] = compute_signature(body, "secret")
    body["messages"] = [{"role": "user", "content": "forged"}]
    response = await rtc_api.chat_callback(Request(body), "callback1")
    assert response.status_code == 403
    assert json.loads(response.body) == {"text": "signature verification failed"}


async def test_callback_rejects_replayed_event_id(monkeypatch):
    from services.rate_limit import RateLimitDecision

    class AllowLimiter:
        async def consume_callback(self, *_args, **_kwargs):
            return RateLimitDecision(True, 0)

    monkeypatch.setattr(rtc_api.settings, "RTC_CALLBACK_SECRET", "secret")
    monkeypatch.setattr(rtc_api, "verify_callback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(rtc_api, "get_rate_limiter", lambda: AllowLimiter())

    async def already_seen(_event_id):
        return False

    monkeypatch.setattr(rtc_api, "claim_callback_replay", already_seen)
    response = await rtc_api.chat_callback(
        Request({
            "Signature": "ok",
            "EventId": "dup-1",
            "EventTime": "1700000000",
            "messages": [{"role": "user", "content": "hello"}],
        }),
        "callback1",
    )
    assert response.status_code == 403
    assert json.loads(response.body) == {"text": "signature verification failed"}


async def test_callback_rejects_non_object_json_body(monkeypatch):
    monkeypatch.setattr(rtc_api.settings, "RTC_CALLBACK_SECRET", "secret")
    response = await rtc_api.chat_callback(Request(["not", "an", "object"]), "callback1")
    assert response.status_code == 403
    assert json.loads(response.body) == {"text": "signature verification failed"}


async def test_callback_returns_429_when_callback_quota_exceeded(monkeypatch):
    from services.rate_limit import RateLimitDecision

    class DenyLimiter:
        async def consume_callback(self, client_ip, callback_id):
            assert callback_id == "callback1"
            return RateLimitDecision(False, 15)

    monkeypatch.setattr(rtc_api.settings, "RTC_CALLBACK_SECRET", "secret")
    monkeypatch.setattr(rtc_api, "verify_callback", lambda *_args, **_kwargs: True)
    claims = []

    async def claimed(_event_id):
        claims.append(_event_id)
        return True

    monkeypatch.setattr(rtc_api, "claim_callback_replay", claimed)
    monkeypatch.setattr(rtc_api, "get_rate_limiter", lambda: DenyLimiter())
    with pytest.raises(rtc_api.HTTPException) as exc_info:
        await rtc_api.chat_callback(
            Request({
                "Signature": "ok",
                "EventId": "evt-quota",
                "EventTime": "1700000000",
                "messages": [{"role": "user", "content": "你好"}],
            }),
            "callback1",
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "15"
    assert claims == []


class RecordingLock:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    @asynccontextmanager
    async def lease_wait(self, resource_id, *, timeout):
        self.calls.append((resource_id, timeout))
        if self.error is not None:
            raise self.error
        yield object()


async def sse_body(response) -> str:
    parts = []
    async for chunk in response.body_iterator:
        parts.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(parts)


def _patch_signed_callback(monkeypatch, *, lock=None):
    monkeypatch.setattr(rtc_api.settings, "RTC_CALLBACK_SECRET", "secret")
    monkeypatch.setattr(rtc_api, "verify_callback", lambda *_args, **_kwargs: True)

    async def claimed(_event_id):
        return True

    monkeypatch.setattr(rtc_api, "claim_callback_replay", claimed)
    monkeypatch.setattr(
        rtc_api.storage, "session_get_by_callback", lambda *_args: async_value(SESSION),
    )
    monkeypatch.setattr(rtc_api, "get_rtc_lock", lambda: lock or RecordingLock())

    class AllowLimiter:
        async def consume_callback(self, *_args, **_kwargs):
            from services.rate_limit import RateLimitDecision
            return RateLimitDecision(True, 0)

    monkeypatch.setattr(rtc_api, "get_rate_limiter", lambda: AllowLimiter())


USER_CALLBACK = {
    "Signature": "ok",
    "EventId": "evt-hot",
    "EventTime": "1700000000",
    "messages": [{"role": "user", "content": "你好"}],
}


@pytest.mark.parametrize(
    "error",
    [
        ColdPathStateError("PREVIOUS_COLD_PATH_FAILED"),
        ColdPathStateError("PREVIOUS_COLD_PATH_PENDING"),
    ],
)
async def test_callback_speaks_error_when_cold_path_blocks_hot_path(monkeypatch, error):
    waits = []
    lock = RecordingLock()
    _patch_signed_callback(monkeypatch, lock=lock)

    async def blocked(session_id, timeout=None, **_kwargs):
        waits.append((session_id, timeout))
        raise error

    monkeypatch.setattr(rtc_api, "await_pending_cold", blocked)
    response = await rtc_api.chat_callback(Request(USER_CALLBACK), "callback1")
    body = await sse_body(response)
    assert ERROR_REPLY in body
    assert "data: [DONE]" in body
    assert waits == [("s1", isv.HOT_PATH_COLD_WAIT_SECONDS)]
    assert lock.calls == [("s1", RTC_LOCK_WAIT_SECONDS)]


@pytest.mark.parametrize(
    "error",
    [LockBusy("busy"), LockLost("lost"), SharedStateUnavailable("down")],
)
async def test_callback_speaks_error_when_session_lock_unavailable(monkeypatch, error):
    lock = RecordingLock(error=error)
    _patch_signed_callback(monkeypatch, lock=lock)
    cold_calls = []

    async def forbidden(*_args, **_kwargs):
        cold_calls.append(True)
        raise AssertionError("locked callback must not wait on cold path")

    monkeypatch.setattr(rtc_api, "await_pending_cold", forbidden)
    response = await rtc_api.chat_callback(Request(USER_CALLBACK), "callback1")
    body = await sse_body(response)
    assert ERROR_REPLY in body
    assert "data: [DONE]" in body
    assert lock.calls == [("s1", RTC_LOCK_WAIT_SECONDS)]
    assert cold_calls == []


async def test_callback_speaks_error_when_hot_path_raises(monkeypatch):
    _patch_signed_callback(monkeypatch, lock=RecordingLock())

    async def ready(*_args, **_kwargs):
        return None

    async def boom(*_args, **_kwargs):
        raise RuntimeError("checkpoint exploded")

    monkeypatch.setattr(rtc_api, "await_pending_cold", ready)
    monkeypatch.setattr(rtc_api, "_restore_state", boom)
    response = await rtc_api.chat_callback(Request(USER_CALLBACK), "callback1")
    body = await sse_body(response)
    assert ERROR_REPLY in body
    assert "data: [DONE]" in body


class FakeGraph:
    async def aupdate_state(self, *_args, **_kwargs):
        return None


async def _ready(*_args, **_kwargs):
    return None


async def _intro_state(_session):
    return {"stage": "intro", "round_no": 0, "messages": []}


async def test_callback_gate_reply_does_not_append_error_chunk(monkeypatch):
    _patch_signed_callback(monkeypatch, lock=RecordingLock())
    monkeypatch.setattr(rtc_api, "await_pending_cold", _ready)
    monkeypatch.setattr(rtc_api, "_restore_state", _intro_state)
    monkeypatch.setattr(rtc_api, "get_graph", lambda: FakeGraph())
    response = await rtc_api.chat_callback(Request(USER_CALLBACK), "callback1")
    body = await sse_body(response)
    assert isv.WAITING_PROMPT in body
    assert ERROR_REPLY not in body
    assert body.count("data: [DONE]") == 1


async def test_callback_does_not_speak_error_when_lock_release_fails(monkeypatch):
    class ExitFailLock:
        @asynccontextmanager
        async def lease_wait(self, resource_id, *, timeout):
            yield object()
            raise LockLost("lost on exit")

    _patch_signed_callback(monkeypatch, lock=ExitFailLock())
    monkeypatch.setattr(rtc_api, "await_pending_cold", _ready)
    monkeypatch.setattr(rtc_api, "_restore_state", _intro_state)
    monkeypatch.setattr(rtc_api, "get_graph", lambda: FakeGraph())
    response = await rtc_api.chat_callback(Request(USER_CALLBACK), "callback1")
    body = await sse_body(response)
    assert isv.WAITING_PROMPT in body
    assert ERROR_REPLY not in body
    assert body.count("data: [DONE]") == 1
