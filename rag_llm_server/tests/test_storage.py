"""Storage CRUD and local file storage tests."""
import pytest

from services.storage.base import StorageConflictError
from services.storage.file_storage import LocalFileStorage
from services.storage.sqlite import SqliteStorage


@pytest.fixture
async def storage(tmp_path):
    value = SqliteStorage(str(tmp_path / "test.db"))
    await value.init()
    value.test_user_id = (await value.user_create("test-user", "hash"))["id"]
    yield value
    await value.close()


async def create_session(storage, **overrides):
    payload = {
        "resume_id": None,
        "position": "Java backend",
        "stage": "intro",
        "status": "running",
        **overrides,
    }
    return await storage.session_create(storage.test_user_id, payload)


async def test_resume_crud_roundtrip(storage):
    user_id = storage.test_user_id
    resume = await storage.resume_create(
        user_id,
        {"content": "resume", "structured_json": None, "source": "md", "status": "parsing"},
    )
    assert await storage.resume_get(user_id, resume["id"]) == resume
    updated = await storage.resume_update(
        user_id, resume["id"], {"status": "ready", "structured_json": "{}"},
    )
    assert updated["status"] == "ready" and updated["structured_json"] == "{}"


async def test_resume_latest_orders_by_created(storage):
    user_id = storage.test_user_id
    await storage.resume_create(user_id, {"content": "a", "source": "md", "status": "parsing"})
    second = await storage.resume_create(
        user_id, {"content": "b", "source": "md", "status": "parsing"},
    )
    assert (await storage.resume_latest(user_id))["id"] == second["id"]
    assert len(await storage.resume_list(user_id)) == 2


async def test_session_running_filter(storage):
    running_session = await create_session(storage)
    await create_session(storage, stage="finish", status="finished")
    running = await storage.session_list_running(storage.test_user_id)
    assert [item["id"] for item in running] == [running_session["id"]]


async def test_second_running_session_conflicts(storage):
    await create_session(storage)
    with pytest.raises(StorageConflictError):
        await create_session(storage)


async def test_session_update(storage):
    session = await create_session(storage)
    updated = await storage.session_update(
        storage.test_user_id, session["id"], {"stage": "deepdive"},
    )
    assert updated["stage"] == "deepdive" and updated["status"] == "running"


async def test_message_seq_autoincrement(storage):
    first = await create_session(storage)
    await storage.session_update(storage.test_user_id, first["id"], {"status": "abandoned"})
    second = await create_session(storage)
    user_id = storage.test_user_id
    await storage.message_append(user_id, first["id"], "user", "hello")
    message2 = await storage.message_append(user_id, first["id"], "assistant", "welcome")
    message3 = await storage.message_append(user_id, second["id"], "user", "another session")
    assert message2["seq"] == 2 and message3["seq"] == 1
    messages = await storage.message_list(user_id, first["id"])
    assert [message["seq"] for message in messages] == [1, 2]


async def test_report_crud(storage):
    session = await create_session(storage)
    user_id = storage.test_user_id
    report = await storage.report_create(
        user_id,
        {
            "session_id": session["id"],
            "scores_json": "{}",
            "feedback_json": "[]",
            "suggestions_json": "[]",
        },
    )
    assert (await storage.report_get(user_id, report["id"]))["session_id"] == session["id"]
    assert (await storage.report_get_by_session(user_id, session["id"]))["id"] == report["id"]
    assert len(await storage.report_list(user_id)) == 1


async def test_file_storage_roundtrip(tmp_path):
    storage = LocalFileStorage(str(tmp_path / "reports"))
    path = await storage.save_text("s1/report.md", "# report")
    assert await storage.read_text("s1/report.md") == "# report"
    assert path.endswith("report.md")


async def test_update_ignores_unknown_columns(storage):
    user_id = storage.test_user_id
    resume = await storage.resume_create(
        user_id, {"content": "c", "source": "md", "status": "parsing"},
    )
    updated = await storage.resume_update(
        user_id, resume["id"], {"status": "ready", "unknown_col": "x"},
    )
    assert updated["id"] == resume["id"] and updated["status"] == "ready"


async def test_update_empty_patch_returns_current_row(storage):
    user_id = storage.test_user_id
    resume = await storage.resume_create(
        user_id, {"content": "c", "source": "md", "status": "parsing"},
    )
    updated = await storage.resume_update(user_id, resume["id"], {})
    assert updated["id"] == resume["id"] and updated["status"] == "parsing"
    session = await create_session(storage)
    session_updated = await storage.session_update(user_id, session["id"], {})
    assert session_updated["stage"] == "intro" and session_updated["status"] == "running"


async def test_message_append_returns_full_row(storage):
    session = await create_session(storage)
    message = await storage.message_append(
        storage.test_user_id, session["id"], "user", "hello",
    )
    assert message["seq"] == 1 and "id" in message and "created_at" in message


async def test_file_storage_path_traversal_guard(tmp_path):
    storage = LocalFileStorage(str(tmp_path / "reports"))
    with pytest.raises(ValueError):
        await storage.save_text("../escape.md", "# x")
    with pytest.raises(ValueError):
        await storage.read_text("../../escape.md")


async def test_report_create_persists_position(storage):
    user_id = storage.test_user_id
    session = await create_session(storage)
    report = await storage.report_create(
        user_id,
        {
            "session_id": session["id"],
            "scores_json": "{}",
            "feedback_json": "{}",
            "suggestions_json": "[]",
            "position": "Java backend",
        },
    )
    assert (await storage.report_get(user_id, report["id"]))["position"] == "Java backend"


async def test_init_migrates_legacy_db_without_position_column(tmp_path):
    import sqlite3

    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(str(db_path))
    connection.execute(
        "CREATE TABLE IF NOT EXISTS interview_report ("
        "id TEXT PRIMARY KEY, session_id TEXT, scores_json TEXT, "
        "feedback_json TEXT, suggestions_json TEXT, md_path TEXT, created_at TEXT)"
    )
    connection.commit()
    connection.close()

    storage = SqliteStorage(str(db_path))
    await storage.init()
    await storage.init()
    user_id = (await storage.user_create("legacy-user", "hash"))["id"]
    report = await storage.report_create(
        user_id,
        {
            "session_id": None,
            "scores_json": "{}",
            "feedback_json": "{}",
            "suggestions_json": "[]",
            "position": "Java backend",
        },
    )
    assert (await storage.report_get(user_id, report["id"]))["position"] == "Java backend"
    await storage.close()
