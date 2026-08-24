import pytest

from services.auth_service import hash_password


@pytest.mark.asyncio
async def test_owned_resources_are_invisible_to_other_users(tmp_storage):
    storage = await tmp_storage()
    alice = await storage.user_create("alice", hash_password("password-123"))
    bob = await storage.user_create("bob", hash_password("password-123"))

    resume = await storage.resume_create(alice["id"], {
        "content": "alice resume", "source": "md", "status": "ready",
    })
    session = await storage.session_create(alice["id"], {
        "resume_id": resume["id"], "position": "backend", "stage": "intro",
        "status": "running",
    })
    report = await storage.report_create(alice["id"], {
        "session_id": session["id"], "scores_json": "{}", "feedback_json": "{}",
        "suggestions_json": "[]",
    })
    recording = await storage.recording_create(alice["id"], {"filename": "a.mp3", "ext": "mp3"})

    assert await storage.resume_get(bob["id"], resume["id"]) is None
    assert await storage.session_get(bob["id"], session["id"]) is None
    assert await storage.report_get(bob["id"], report["id"]) is None
    assert await storage.recording_get(bob["id"], recording["id"]) is None
    assert await storage.resume_list(bob["id"]) == []
    assert await storage.report_list(bob["id"]) == []
    await storage.close()


@pytest.mark.asyncio
async def test_rtc_identifiers_are_unique_per_session(tmp_storage):
    storage = await tmp_storage()
    user = await storage.user_create("alice", hash_password("password-123"))
    first = await storage.session_create(user["id"], {
        "resume_id": None, "position": "backend", "stage": "intro", "status": "running",
    })
    second = await storage.session_create(user["id"], {
        "resume_id": None, "position": "backend", "stage": "intro", "status": "running",
    })

    for field in ("rtc_room_id", "rtc_user_id", "rtc_task_id", "rtc_callback_id"):
        assert first[field]
        assert first[field] != second[field]
    assert first["rtc_status"] == "created"
    assert await storage.session_get_by_callback(first["rtc_callback_id"]) == first
    await storage.close()
