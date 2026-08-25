import json
import os
import uuid

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.models import AppUser, Message, Resume
from scripts.migrate_sqlite_to_postgres import migrate_database
from scripts.verify_data_migration import verify_database
from tests.test_data_migration import _build_source


@pytest.fixture
async def verifier_target():
    load_dotenv()
    target = os.getenv("MIGRATION_DATABASE_URL")
    if not target:
        pytest.skip("MIGRATION_DATABASE_URL is required for verifier tests")
    engine = create_async_engine(target)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"verifier_{uuid.uuid4().hex[:10]}"
    try:
        yield target, sessions, prefix
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(AppUser).where(AppUser.username.like(f"{prefix}%"))
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_verifier_accepts_complete_migration(tmp_path, verifier_target):
    target, _, prefix = verifier_target
    source = tmp_path / "generated-verified.db"
    await _build_source(source, prefix)
    await migrate_database(source, target, batch_size=2)

    result = await verify_database(source, target)

    assert result.ok is True
    assert result.source_fk_errors == result.target_fk_errors == 0
    assert result.source_owner_errors == result.target_owner_errors == 0
    assert "fixture" not in json.dumps(result.safe_report())
    assert result.safe_report()["tables"]["resume"]["ok"] is True


@pytest.mark.asyncio
async def test_verifier_rejects_deleted_target_row(tmp_path, verifier_target):
    target, sessions, prefix = verifier_target
    source = tmp_path / "generated-deleted.db"
    await _build_source(source, prefix)
    await migrate_database(source, target)
    async with sessions.begin() as session:
        await session.execute(
            delete(Message).where(Message.content == "fixture question")
        )

    result = await verify_database(source, target)

    assert result.ok is False
    assert result.tables["message"].source_count != result.tables["message"].target_count


@pytest.mark.asyncio
async def test_verifier_rejects_tampered_target_row(tmp_path, verifier_target):
    target, sessions, prefix = verifier_target
    source = tmp_path / "generated-tampered.db"
    ids = await _build_source(source, prefix)
    await migrate_database(source, target)
    async with sessions.begin() as session:
        await session.execute(
            update(Resume)
            .where(Resume.id == uuid.UUID(ids["resume"]))
            .values(content="tampered")
        )

    result = await verify_database(source, target)

    assert result.ok is False
    assert result.tables["resume"].source_row_hash != result.tables["resume"].target_row_hash
