import asyncio
import os
import uuid

import pytest
from dotenv import load_dotenv
import psycopg
from sqlalchemy import delete

from config import Config
from db.engine import build_database_runtime
from db.models import AppUser
from mcp.postgres_server import MAX_ROWS, query, validate_query
from services.storage.postgres import PostgresRepository


def test_postgres_select_and_cte_get_stable_limit():
    assert validate_query("SELECT id FROM resume").endswith(f"LIMIT {MAX_ROWS}")
    safe = validate_query(
        "WITH recent AS (SELECT id FROM interview_session) SELECT id FROM recent"
    )
    assert safe.startswith("WITH recent AS")
    assert safe.endswith(f"LIMIT {MAX_ROWS}")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM resume; SELECT * FROM resume",
        "SELECT * FROM resume -- bypass",
        "SELECT * FROM resume /* bypass */",
        "WITH removed AS (DELETE FROM resume RETURNING *) SELECT * FROM removed",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM public.resume",
        "SELECT * FROM checkpoint_migrations",
        "SELECT pg_sleep(10) FROM resume",
        "SELECT * INTO copied FROM resume",
        "SELECT * FROM resume FOR UPDATE",
    ],
)
def test_postgres_guard_rejects_bypass_and_system_access(sql):
    with pytest.raises(ValueError):
        validate_query(sql)


def test_postgres_limit_boundaries():
    assert validate_query("SELECT * FROM message LIMIT 500").endswith("LIMIT 500")
    with pytest.raises(ValueError):
        validate_query("SELECT * FROM message LIMIT 501")
    with pytest.raises(ValueError):
        validate_query("SELECT * FROM message LIMIT ALL")


def test_postgres_analytics_views_enforce_tenant_isolation(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL") or not os.getenv("ANALYTICS_DATABASE_URL"):
        pytest.skip("PostgreSQL application and analytics DSNs are required")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")

    async def verify():
        runtime = build_database_runtime(Config())
        await runtime.start()
        prefix = f"analytics_{uuid.uuid4().hex[:10]}"
        try:
            async with runtime.session_scope() as session:
                repository = PostgresRepository(session)
                alice = await repository.user_create(f"{prefix}_alice", "hash")
                bob = await repository.user_create(f"{prefix}_bob", "hash")
                await repository.resume_create(
                    alice["id"],
                    {"content": "alice-only", "source": "text", "status": "ready"},
                )
                await repository.resume_create(
                    bob["id"],
                    {"content": "bob-only", "source": "text", "status": "ready"},
                )
            result = await query("SELECT content FROM resume ORDER BY content", alice["id"])
            assert result.items == [{"content": "alice-only"}]
            assert result.truncated is False
        finally:
            async with runtime.session_scope() as session:
                await session.execute(
                    delete(AppUser).where(AppUser.username.like(f"{prefix}%"))
                )
            await runtime.close()

    if os.name == "nt":
        asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(verify())


def test_postgres_analytics_role_cannot_write():
    load_dotenv()
    database_url = os.getenv("ANALYTICS_DATABASE_URL")
    if not database_url:
        pytest.skip("ANALYTICS_DATABASE_URL is required")
    conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, connect_timeout=5) as connection:
        with pytest.raises(
            (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)
        ):
            connection.execute(
                "INSERT INTO public.app_user (id, username, password_hash) VALUES (%s, %s, %s)",
                (uuid.uuid4(), "analytics-write-denied", "hash"),
            )
