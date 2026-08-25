import asyncio
import os
import uuid
from contextlib import asynccontextmanager

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete

from config import Config
from db.engine import build_database_runtime
from db.models import AppUser
from services.storage.base import StorageConflictError
from services.storage.postgres import PostgresAuthRepository
from services.storage.sqlite import SqliteStorage


@pytest.fixture(params=["sqlite", "postgres"])
async def auth_repository_scope(request, tmp_path):
    prefix = f"contract_{uuid.uuid4().hex[:10]}"
    if request.param == "sqlite":
        path = str(tmp_path / "contract.db")
        bootstrap = SqliteStorage(path)
        await bootstrap.init()
        await bootstrap.close()

        @asynccontextmanager
        async def scope():
            repository = SqliteStorage(path)
            await repository.init()
            try:
                yield repository
            finally:
                await repository.close()

        yield prefix, scope
        return

    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for PostgreSQL contract tests")
    os.environ["APP_ENV"] = "test"
    os.environ["STORAGE_BACKEND"] = "postgres"
    runtime = build_database_runtime(Config())
    await runtime.start()

    @asynccontextmanager
    async def scope():
        async with runtime.session_scope() as session:
            yield PostgresAuthRepository(session)

    try:
        yield prefix, scope
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.username.like(f"{prefix}%")))
        await runtime.close()


async def test_user_and_auth_session_contract(auth_repository_scope):
    prefix, scope = auth_repository_scope
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


async def test_concurrent_duplicate_username_has_one_winner(auth_repository_scope):
    prefix, scope = auth_repository_scope
    username = f"{prefix}_duplicate"

    async def create_user():
        async with scope() as repository:
            return await repository.user_create(username, "password-hash")

    results = await asyncio.gather(create_user(), create_user(), return_exceptions=True)

    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, StorageConflictError) for result in results) == 1
