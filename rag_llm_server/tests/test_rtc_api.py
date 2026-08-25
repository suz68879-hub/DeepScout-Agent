"""RTC router compatibility tests for explicit interview sessions."""
import json

import pytest

import api.rtc as rtc_api
from services.distributed_lock import LockBusy, LockLost
from services.redis_client import SharedStateUnavailable


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
