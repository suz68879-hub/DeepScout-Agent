"""协议层 SQL 安全：非法查询全部拒绝，合法查询自动补 LIMIT。"""
import pytest

from mcp.sqlite_server import MAX_LIMIT, query, validate_query


def test_valid_select_gets_forced_limit():
    safe = validate_query("SELECT id, status FROM resume")
    assert safe.endswith(f"LIMIT {MAX_LIMIT}")


def test_limit_over_max_rejected():
    """R-T14-3：count > 100 由「改写为 100」改为拒绝（裁定 2，覆盖 brief 原 cap 行为）。"""
    with pytest.raises(ValueError):
        validate_query("SELECT * FROM message LIMIT 500")


def test_limit_within_max_kept():
    safe = validate_query("SELECT * FROM message LIMIT 10")
    assert safe.endswith("LIMIT 10")


@pytest.mark.parametrize("sql", [
    "DROP TABLE resume",
    "DELETE FROM resume",
    "INSERT INTO resume VALUES (1)",
    "UPDATE resume SET status='x'",
    "ALTER TABLE resume ADD COLUMN x",
    "ATTACH DATABASE 'x' AS y",
    "PRAGMA table_info(resume)",
    "SELECT * FROM resume UNION SELECT * FROM interview_session",
    "SELECT * FROM resume; DROP TABLE resume",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM users",
    "WITH x AS (SELECT 1) SELECT * FROM x",
])
def test_illegal_sql_rejected(sql):
    with pytest.raises(ValueError):
        validate_query(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM resume, interview_session",
    "SELECT * FROM resume, checkpoint",
])
def test_multi_table_from_rejected(sql):
    """R-T14-2：逗号多表仅提取首个表名即可绕过白名单正则，必须直接拒绝。"""
    with pytest.raises(ValueError):
        validate_query(sql)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """150 行 message 的临时库（验证 LIMIT 不变式在真实执行中生效）。"""
    import asyncio
    import sqlite3

    from config import settings
    from services.storage.sqlite import SqliteStorage

    db = str(tmp_path / "big.db")
    monkeypatch.setattr(settings, "DATABASE_PATH", db)
    s = SqliteStorage()
    asyncio.run(s.init())
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO message (session_id, role, content, seq, created_at)"
        " VALUES (NULL, NULL, NULL, NULL, NULL)",
        [()] * 150,
    )
    conn.commit()
    conn.close()
    yield db
    asyncio.run(s.close())


def test_limit_in_string_literal_does_not_suppress_forced_limit(seeded_db):
    """R-T14-3：字面量内 'limit 10' 不得压制强制 LIMIT；执行必须 <= 100 行。"""
    safe = validate_query("SELECT 'limit 10' AS x FROM message")
    assert "'limit 10'" in safe
    assert safe.endswith(f"LIMIT {MAX_LIMIT}")
    rows = query("SELECT 'limit 10' AS x FROM message", "u1")
    assert len(rows) <= 100


def test_limit_in_string_literal_not_rewritten(seeded_db):
    """R-T14-3：字面量 'limit 500' 绝不被改写，且另追加真实 LIMIT。"""
    safe = validate_query("SELECT 'limit 500' AS x FROM message")
    assert "'limit 500'" in safe
    assert "500" in safe
    assert safe.endswith(f"LIMIT {MAX_LIMIT}")
    rows = query("SELECT 'limit 500' AS x FROM message", "u1")
    assert len(rows) <= 100


@pytest.mark.parametrize("sql", [
    "SELECT * FROM message LIMIT 100, 999999",
    "SELECT * FROM message LIMIT 0, 101",
])
def test_limit_offset_count_over_max_rejected(sql):
    """R-T14-3：LIMIT offset, count 形态必须校验 count 段。"""
    with pytest.raises(ValueError):
        validate_query(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM message LIMIT -1",
    "SELECT * FROM message LIMIT 0, -1",
])
def test_limit_negative_rejected(sql):
    """R-T14-3：负数 LIMIT（SQLite 视为无上限）必须拒绝。"""
    with pytest.raises(ValueError):
        validate_query(sql)


def test_limit_offset_count_within_max_passes():
    safe = validate_query("SELECT * FROM message LIMIT 100, 5")
    assert safe == "SELECT * FROM message LIMIT 100, 5"


def test_limit_in_line_comment_ignored(seeded_db):
    """R-T14-3：行注释内 'limit 10' 不算 LIMIT；强制 LIMIT 须插入注释之前。"""
    safe = validate_query("SELECT * FROM message -- limit 10")
    assert "-- limit 10" in safe
    assert safe.index("LIMIT 100") < safe.index("--")
    rows = query("SELECT * FROM message -- limit 10", "u1")
    assert len(rows) <= 100


def test_subquery_references_checkpoint_rejected():
    """R-T14-3：子查询引用 checkpoint（同库 LangGraph 表）必须拒绝。"""
    with pytest.raises(ValueError):
        validate_query("SELECT * FROM resume WHERE id IN (SELECT id FROM checkpoint)")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM main.resume",
    "SELECT * FROM temp.resume",
])
def test_schema_qualification_cannot_bypass_tenant_views(sql):
    with pytest.raises(ValueError):
        validate_query(sql)


def test_query_only_returns_current_user_rows(tmp_path, monkeypatch):
    import asyncio
    from config import settings
    from services.auth_service import hash_password
    from services.storage.sqlite import SqliteStorage

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "tenant.db"))
    storage = SqliteStorage()
    asyncio.run(storage.init())
    alice = asyncio.run(storage.user_create("alice", hash_password("password-123")))
    bob = asyncio.run(storage.user_create("bob", hash_password("password-123")))
    asyncio.run(storage.resume_create(alice["id"], {
        "content": "alice", "source": "md", "status": "ready",
    }))
    asyncio.run(storage.resume_create(bob["id"], {
        "content": "bob", "source": "md", "status": "ready",
    }))

    rows = query("SELECT content FROM resume", alice["id"])
    assert rows == [{"content": "alice"}]
    asyncio.run(storage.close())


def test_query_executes_readonly(tmp_path, monkeypatch):
    import asyncio
    from config import settings
    from services.storage.sqlite import SqliteStorage

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "g.db"))
    s = SqliteStorage()
    asyncio.run(s.init())
    user = asyncio.run(s.user_create("query-user", "hash"))
    asyncio.run(s.resume_create(user["id"], {"content": "x", "source": "md", "status": "ready"}))
    rows = query("SELECT COUNT(*) AS total FROM resume", user["id"])
    assert rows[0]["total"] == 1
    asyncio.run(s.close())


def test_query_rejects_write_through_connection(tmp_path, monkeypatch):
    import asyncio
    from config import settings
    from services.storage.sqlite import SqliteStorage

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "w.db"))
    s = SqliteStorage()
    asyncio.run(s.init())
    with pytest.raises(ValueError):
        query("DROP TABLE resume", "u1")
    asyncio.run(s.close())
