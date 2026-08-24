import pytest

import services.rtc_service as rtc_service


def _session(**overrides):
    row = {
        "id": "session-1",
        "user_id": "owner-1",
        "rtc_room_id": "room-1",
        "rtc_user_id": "rtc-user-1",
        "rtc_task_id": "task-1",
        "rtc_callback_id": "callback-1",
        "rtc_status": "created",
        "position": "backend",
        "resume_id": None,
    }
    row.update(overrides)
    return row


def test_scene_payload_uses_session_rtc_identifiers(monkeypatch):
    monkeypatch.setattr(rtc_service.settings, "RTC_APP_ID", "app")
    monkeypatch.setattr(rtc_service.settings, "RTC_APP_KEY", "key")
    payload = rtc_service.get_scenes_payload(_session())
    scene = payload["Result"]["scenes"][0]
    assert scene["rtc"]["RoomId"] == "room-1"
    assert scene["rtc"]["UserId"] == "rtc-user-1"
    assert scene["rtc"]["SessionId"] == "session-1"


async def test_start_body_contains_owner_session_callback(monkeypatch):
    monkeypatch.setattr(rtc_service.settings, "SERVER_URL", "https://example.test/base/")
    monkeypatch.setattr(rtc_service.settings, "RTC_CALLBACK_SECRET", "callback-secret")

    async def restore(_session):
        return {"position": "backend"}

    monkeypatch.setattr(rtc_service, "_restore_state", restore)
    body = await rtc_service.build_voice_chat_body("StartVoiceChat", _session(), {})
    assert body["RoomId"] == "room-1"
    assert body["TaskId"] == "task-1"
    assert body["AgentConfig"]["TargetUserId"] == ["rtc-user-1"]
    assert body["AgentConfig"]["ServerMessageURLForRTS"] == (
        "https://example.test/base/api/chat_callback?rtc_callback_id=callback-1"
    )
    assert body["AgentConfig"]["ServerMessageSignatureForRTS"] == "callback-secret"


async def test_repeated_start_is_idempotent(monkeypatch):
    session = _session(rtc_status="running")
    calls = []

    async def forbidden_post(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("third party must not be called")

    monkeypatch.setattr(rtc_service.httpx.AsyncClient, "post", forbidden_post)
    result = await rtc_service.call_voice_chat_openapi(
        "StartVoiceChat", "2024-12-01", session, {"SessionId": session["id"]},
    )
    assert calls == []
    assert result["ResponseMetadata"]["Action"] == "StartVoiceChat"
    assert result["Result"]["Idempotent"] is True


async def test_start_requires_server_url(monkeypatch):
    monkeypatch.setattr(rtc_service.settings, "SERVER_URL", None)
    with pytest.raises(rtc_service.RTCConfigurationError):
        await rtc_service.build_voice_chat_body("StartVoiceChat", _session(), {})
