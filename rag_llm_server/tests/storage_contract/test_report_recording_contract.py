import pytest

from services.storage.base import StorageConflictError


async def _users_and_session(prefix, scope):
    async with scope() as repository:
        alice = await repository.user_create(f"{prefix}_alice", "hash")
        bob = await repository.user_create(f"{prefix}_bob", "hash")
        session = await repository.session_create(
            alice["id"],
            {"position": "Backend", "stage": "closing", "status": "completed"},
        )
    return alice, bob, session


async def test_report_owner_and_duplicate_contract(repository_scope):
    prefix, scope = repository_scope
    alice, bob, session = await _users_and_session(prefix, scope)
    payload = {
        "session_id": session["id"],
        "scores_json": '{"technical":90}',
        "feedback_json": '{"summary":"good"}',
        "suggestions_json": '["practice"]',
        "position": "Backend",
        "source": "session",
    }
    async with scope() as repository:
        report = await repository.report_create(alice["id"], payload)

    async with scope() as repository:
        assert await repository.report_get(bob["id"], report["id"]) is None
        with pytest.raises(ValueError, match="session does not belong"):
            await repository.report_create(bob["id"], payload)

    with pytest.raises(StorageConflictError):
        async with scope() as repository:
            await repository.report_create(alice["id"], payload)


async def test_recording_owner_update_and_processing_contract(repository_scope):
    prefix, scope = repository_scope
    alice, bob, session = await _users_and_session(prefix, scope)
    async with scope() as repository:
        report = await repository.report_create(
            alice["id"],
            {
                "session_id": session["id"],
                "scores_json": "{}",
                "feedback_json": "{}",
                "suggestions_json": "[]",
            },
        )
        recording = await repository.recording_create(
            alice["id"],
            {"filename": "sample.mp3", "status": "processing", "report_id": report["id"]},
        )

    async with scope() as repository:
        assert await repository.recording_get(bob["id"], recording["id"]) is None
        assert await repository.recording_update(
            bob["id"], recording["id"], {"status": "completed"}
        ) is None
        with pytest.raises(ValueError, match="report does not belong"):
            await repository.recording_create(
                bob["id"], {"filename": "denied.mp3", "report_id": report["id"]}
            )
        processing = await repository.recording_list_processing()
        assert [row["id"] for row in processing if row["id"] == recording["id"]] == [
            recording["id"]
        ]
        assert (await repository.recording_get_internal(recording["id"]))["user_id"] == alice["id"]
