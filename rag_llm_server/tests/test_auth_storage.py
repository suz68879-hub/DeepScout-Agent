import hashlib
import sqlite3

import pytest

from config import settings
from services.auth_service import (
    create_session_token,
    hash_password,
    normalize_username,
    verify_password,
)
from services.storage.sqlite import SqliteStorage


def test_username_and_password_contract():
    assert normalize_username("Alice_01") == "alice_01"
    with pytest.raises(ValueError):
        normalize_username("ab")
    with pytest.raises(ValueError):
        normalize_username("用户")

    password_hash = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", password_hash)
    assert not verify_password("wrong password", password_hash)


@pytest.mark.asyncio
async def test_auth_session_stores_only_token_digest(tmp_path):
    storage = SqliteStorage(str(tmp_path / "auth.db"))
    await storage.init()
    user = await storage.user_create("alice", hash_password("password-123"))

    token, token_hash, expires_at = create_session_token()
    await storage.auth_session_create(user["id"], token_hash, expires_at)

    assert token != token_hash
    assert hashlib.sha256(token.encode()).hexdigest() == token_hash
    assert await storage.auth_session_get_user(token_hash) == user
    await storage.close()


@pytest.mark.asyncio
async def test_legacy_rows_are_assigned_to_bootstrap_admin(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE resume (id TEXT PRIMARY KEY, content TEXT, structured_json TEXT, "
            "source TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO resume VALUES ('legacy-resume', 'legacy', NULL, 'md', 'ready', 'now', 'now')"
        )

    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_USERNAME", "Owner.Admin")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_PASSWORD", "password-123")
    storage = SqliteStorage(str(db_path))
    await storage.init()

    admin = await storage.user_get_by_username("owner.admin")
    assert admin is not None
    assert admin["role"] == "admin"
    assert (await storage.resume_get(admin["id"], "legacy-resume"))["content"] == "legacy"
    assert await storage.resume_get("someone-else", "legacy-resume") is None
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 2
    await storage.close()


@pytest.mark.asyncio
async def test_legacy_migration_requires_bootstrap_credentials(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE resume (id TEXT PRIMARY KEY, content TEXT, structured_json TEXT, "
            "source TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO resume VALUES ('legacy-resume', 'legacy', NULL, 'md', 'ready', 'now', 'now')"
        )

    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_USERNAME", None)
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_PASSWORD", None)
    storage = SqliteStorage(str(db_path))
    with pytest.raises(RuntimeError, match="BOOTSTRAP_ADMIN"):
        await storage.init()
    await storage.close()


@pytest.mark.asyncio
async def test_legacy_migration_rejects_broken_ownership_links(tmp_path, monkeypatch):
    db_path = tmp_path / "broken.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE interview_session (id TEXT PRIMARY KEY, resume_id TEXT, position TEXT, "
            "stage TEXT, status TEXT, started_at TEXT, ended_at TEXT)"
        )
        conn.execute(
            "INSERT INTO interview_session VALUES "
            "('session-1', 'missing-resume', 'Java', 'intro', 'running', 'now', NULL)"
        )

    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_USERNAME", "owner-admin")
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMIN_PASSWORD", "password-123")
    storage = SqliteStorage(str(db_path))
    with pytest.raises(RuntimeError, match="ownership audit"):
        await storage.init()
    await storage.close()
