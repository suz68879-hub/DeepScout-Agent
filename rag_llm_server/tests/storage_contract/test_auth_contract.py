import asyncio

from services.storage.base import StorageConflictError


async def test_user_and_auth_session_contract(repository_scope):
    prefix, scope = repository_scope
    username = f"{prefix}_user"
    async with scope() as repository:
        user = await repository.user_create(username, "password-hash")

    async with scope() as repository:
        assert await repository.user_get_by_username(username) == user
        await repository.auth_session_create(
            user["id"],
            "a" * 64,
            "2999-01-01T00:00:00+00:00",
        )

    async with scope() as repository:
        assert await repository.auth_session_get_user("a" * 64) == user
        await repository.auth_session_revoke("a" * 64)

    async with scope() as repository:
        assert await repository.auth_session_get_user("a" * 64) is None


async def test_concurrent_duplicate_username_has_one_winner(repository_scope):
    prefix, scope = repository_scope
    username = f"{prefix}_duplicate"

    async def create_user():
        async with scope() as repository:
            return await repository.user_create(username, "password-hash")

    results = await asyncio.gather(create_user(), create_user(), return_exceptions=True)

    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, StorageConflictError) for result in results) == 1
