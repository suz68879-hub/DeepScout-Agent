from contextlib import asynccontextmanager

import pytest

import services.rtc_service as rtc_service
from services.distributed_lock import LockLost
from services.storage.base import StorageVersionConflictError


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


async def test_rtc_fencing_rejects_stale_status_update(tmp_storage):
    repository = await tmp_storage()
    owner = await repository.user_create("owner", "hash")
    stranger = await repository.user_create("stranger", "hash")
    session_data = _session()
    session_data.pop("id")
    session = await repository.session_create(owner["id"], session_data)

    assert await repository.session_claim_rtc_fence(
        stranger["id"], session["id"], 1
    ) is None
    claimed = await repository.session_claim_rtc_fence(owner["id"], session["id"], 2)
    assert claimed["rtc_fencing_token"] == 2

    with pytest.raises(StorageVersionConflictError):
        await repository.session_update_rtc_status(
            owner["id"], session["id"], "running", 1
        )
    updated = await repository.session_update_rtc_status(
        owner["id"], session["id"], "running", 2
    )
    assert updated["rtc_status"] == "running"


async def test_lost_lease_stops_start_before_provider_call(monkeypatch):
    session = _session()

    class Lease:
        fencing_token = 1

        async def assert_owned(self):
            raise LockLost("lost")

    class Manager:
        @asynccontextmanager
        async def lease_wait(self, *_args, **_kwargs):
            yield Lease()

    async def get_session(*_args):
        return dict(session)

    async def claim(*_args):
        return {**session, "rtc_fencing_token": 1}

    async def forbidden_provider(*_args):
        raise AssertionError("provider must not run after lease loss")

    monkeypatch.setattr(rtc_service.storage, "session_get", get_session)
    monkeypatch.setattr(rtc_service.storage, "session_claim_rtc_fence", claim)
    monkeypatch.setattr(rtc_service, "get_rtc_lock", lambda: Manager())
    monkeypatch.setattr(rtc_service, "_call_provider", forbidden_provider)

    with pytest.raises(LockLost):
        await rtc_service.call_voice_chat_openapi(
            "StartVoiceChat", "2024-12-01", session, {}
        )
