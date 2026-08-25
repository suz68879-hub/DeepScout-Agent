import hashlib
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from db.models import AppUser, InterviewReport, InterviewSession, Message, Recording, Resume
from scripts.migrate_sqlite_to_postgres import migrate_database
from services.storage.sqlite import SqliteStorage


async def _build_source(path: Path, prefix: str) -> dict[str, str]:
    storage = SqliteStorage(str(path))
    await storage.init()
    await storage.close()
    ids = {name: str(uuid.uuid4()) for name in ("user", "resume", "session", "report", "recording")}
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO app_user VALUES (?, ?, ?, ?, ?)",
            (ids["user"], f"{prefix}_user", "hash", "user", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO resume VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ids["resume"], ids["user"], "fixture", '{"skills":["python"]}', "fixture", "ready", "2026-01-01T00:01:00Z", "2026-01-01T00:01:00Z"),
        )
        connection.execute(
            "INSERT INTO interview_session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ids["session"], ids["user"], ids["resume"], "engineer", "technical", "finished", "2026-01-01T00:02:00Z", "2026-01-01T00:03:00Z", f"{prefix}_room", f"{prefix}_rtc_user", f"{prefix}_task", f"{prefix}_callback", "finished", 0, 1),
        )
        connection.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?, ?, ?)",
            (int(uuid.uuid4().int % 1_000_000_000), ids["session"], "assistant", "fixture question", 1, "2026-01-01T00:02:30Z"),
        )
        connection.execute(
            "INSERT INTO interview_report VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ids["report"], ids["user"], ids["session"], '{"total":90}', '{"summary":"ok"}', '["practice"]', "engineer", "session", f"reports/{prefix}.md", "2026-01-01T00:04:00Z"),
        )
        connection.execute(
            "INSERT INTO recording VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ids["recording"], ids["user"], "fixture.wav", "wav", f"recordings/{prefix}.wav", 16, "finished", None, '{"text":"fixture"}', None, ids["report"], "2026-01-01T00:05:00Z", "2026-01-01T00:06:00Z"),
        )
        connection.commit()
    return ids


@pytest.fixture
async def migration_target():
    load_dotenv()
    target = os.getenv("MIGRATION_DATABASE_URL")
    if not target:
        pytest.skip("MIGRATION_DATABASE_URL is required for migration tests")
    engine = create_async_engine(target)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"migration_{uuid.uuid4().hex[:10]}"
    try:
        yield target, sessions, prefix
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(AppUser).where(AppUser.username.like(f"{prefix}%")))
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_is_repeatable_and_keeps_source_read_only(tmp_path, migration_target):
    target, sessions, prefix = migration_target
    source = tmp_path / "generated-no-pii.db"
    await _build_source(source, prefix)
    before = hashlib.sha256(source.read_bytes()).digest()

    first = await migrate_database(source, target, batch_size=2)
    repeated = await migrate_database(source, target, batch_size=3)

    assert first.total_rows == repeated.total_rows == 6
    assert hashlib.sha256(source.read_bytes()).digest() == before
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AppUser).where(AppUser.username == f"{prefix}_user")) == 1
        for model in (Resume, InterviewSession, Message, InterviewReport, Recording):
            assert await session.scalar(select(func.count()).select_from(model)) >= 1


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(tmp_path, migration_target):
    target, sessions, prefix = migration_target
    source = tmp_path / "generated-dry-run.db"
    await _build_source(source, prefix)

    result = await migrate_database(source, target, batch_size=2, dry_run=True)

    assert result.total_rows == 6
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AppUser).where(AppUser.username == f"{prefix}_user")) == 0


@pytest.mark.asyncio
async def test_resume_from_continues_after_committed_prefix(tmp_path, migration_target):
    target, sessions, prefix = migration_target
    source = tmp_path / "generated-resume.db"
    ids = await _build_source(source, prefix)

    interrupted = await migrate_database(source, target, batch_size=1, stop_after_batches=2)
    assert interrupted.next_resume_from is not None
    resumed = await migrate_database(source, target, batch_size=2, resume_from=interrupted.next_resume_from)

    assert resumed.completed is True
    async with sessions() as session:
        assert await session.get(Recording, uuid.UUID(ids["recording"])) is not None
