import asyncio
import os
import uuid

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete

from config import Config
from db.engine import close_database, init_database
from db.models import AppUser
from services.storage import build_storage
from services.storage.postgres import PostgresStorage
from services.storage.sqlite import SqliteStorage


def test_development_defaults_to_sqlite(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert isinstance(build_storage(Config()), SqliteStorage)


def test_postgres_backend_selects_postgres_storage(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://deepscout_app:test-only@localhost/deepscout_test",
    )

    assert isinstance(build_storage(Config()), PostgresStorage)


def test_production_rejects_sqlite_even_with_database_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://deepscout_app:strong-secret@db.internal/deepscout",
    )

    with pytest.raises(ValueError, match="production requires PostgreSQL"):
        Config()


def test_postgres_storage_facade_roundtrip(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for PostgreSQL integration")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    config = Config()

    async def verify():
        runtime = await init_database(config)
        storage = build_storage(config)
        username = f"selector_{uuid.uuid4().hex[:12]}"
        try:
            await storage.init()
            created = await storage.user_create(username, "hash")
            assert await storage.user_get_by_username(username) == created
        finally:
            if runtime is not None:
                async with runtime.session_scope() as session:
                    await session.execute(delete(AppUser).where(AppUser.username == username))
            await storage.close()
            await close_database()

    if os.name == "nt":
        asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(verify())
