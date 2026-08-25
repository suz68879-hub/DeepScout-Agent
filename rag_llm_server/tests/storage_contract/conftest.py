import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete

from config import Config
from db.engine import build_database_runtime
from db.models import AppUser
from services.storage.postgres import PostgresRepository
from services.storage.sqlite import SqliteStorage


def pytest_asyncio_loop_factories(config, item):
    del config, item
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(params=["sqlite", "postgres"])
async def repository_scope(request, tmp_path, monkeypatch):
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
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    runtime = build_database_runtime(Config())
    await runtime.start()

    @asynccontextmanager
    async def scope():
        async with runtime.session_scope() as session:
            yield PostgresRepository(session)

    try:
        yield prefix, scope
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.username.like(f"{prefix}%")))
        await runtime.close()
