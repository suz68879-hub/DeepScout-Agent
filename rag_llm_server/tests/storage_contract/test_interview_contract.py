import pytest

from services.storage.base import StorageConflictError, StorageVersionConflictError


async def _users(prefix, scope):
    async with scope() as repository:
        alice = await repository.user_create(f"{prefix}_alice", "hash")
        bob = await repository.user_create(f"{prefix}_bob", "hash")
    return alice, bob


async def test_resume_session_message_owner_contract(repository_scope):
    prefix, scope = repository_scope
    alice, bob = await _users(prefix, scope)
    async with scope() as repository:
        resume = await repository.resume_create(
            alice["id"],
            {
                "content": "resume",
                "structured_json": '{"skills":["Python"]}',
                "source": "text",
                "status": "ready",
            },
        )
        session = await repository.session_create(
            alice["id"],
            {
                "resume_id": resume["id"],
                "position": "Backend",
                "stage": "intro",
                "status": "running",
            },
        )
        first = await repository.message_append(alice["id"], session["id"], "user", "one")
        second = await repository.message_append(
            alice["id"], session["id"], "assistant", "two"
        )

    async with scope() as repository:
        assert await repository.resume_get(bob["id"], resume["id"]) is None
        assert await repository.session_get(bob["id"], session["id"]) is None
        assert await repository.message_list(bob["id"], session["id"]) == []
        assert [row["seq"] for row in await repository.message_list(
            alice["id"], session["id"]
        )] == [first["seq"], second["seq"]] == [1, 2]
        with pytest.raises(ValueError, match="session does not belong"):
            await repository.message_append(bob["id"], session["id"], "user", "denied")


async def test_session_optimistic_update_contract(repository_scope):
    prefix, scope = repository_scope
    alice, _ = await _users(prefix, scope)
    async with scope() as repository:
        session = await repository.session_create(
            alice["id"],
            {"position": "Backend", "stage": "intro", "status": "running"},
        )
    assert session["version"] == 1

    async with scope() as repository:
        updated = await repository.session_update(
            alice["id"], session["id"], {"stage": "technical"}, expected_version=1
        )
    assert updated["version"] == 2

    async with scope() as repository:
        with pytest.raises(StorageVersionConflictError):
            await repository.session_update(
                alice["id"], session["id"], {"stage": "closing"}, expected_version=1
            )


async def test_one_running_session_per_user_contract(repository_scope):
    prefix, scope = repository_scope
    alice, _ = await _users(prefix, scope)
    async with scope() as repository:
        await repository.session_create(
            alice["id"],
            {"position": "Backend", "stage": "intro", "status": "running"},
        )

    with pytest.raises(StorageConflictError):
        async with scope() as repository:
            await repository.session_create(
                alice["id"],
                {"position": "Backend", "stage": "intro", "status": "running"},
            )


async def test_rtc_fencing_contract(repository_scope):
    prefix, scope = repository_scope
    alice, bob = await _users(prefix, scope)
    async with scope() as repository:
        session = await repository.session_create(
            alice["id"],
            {"position": "Backend", "stage": "intro", "status": "running"},
        )

    async with scope() as repository:
        assert await repository.session_claim_rtc_fence(
            bob["id"], session["id"], 1
        ) is None
        claimed = await repository.session_claim_rtc_fence(
            alice["id"], session["id"], 2
        )
        assert claimed["rtc_fencing_token"] == 2

    async with scope() as repository:
        with pytest.raises(StorageVersionConflictError):
            await repository.session_update_rtc_status(
                alice["id"], session["id"], "running", 1
            )
        updated = await repository.session_update_rtc_status(
            alice["id"], session["id"], "running", 2
        )
        assert updated["rtc_status"] == "running"
