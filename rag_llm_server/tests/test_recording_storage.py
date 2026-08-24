"""Owned recording and recording-report storage tests."""
import sqlite3

import pytest

from services.storage.sqlite import SqliteStorage


@pytest.fixture
async def storage(tmp_path):
    value = SqliteStorage(str(tmp_path / "test.db"))
    await value.init()
    value.test_user_id = (await value.user_create("recording-user", "hash"))["id"]
    yield value
    await value.close()


async def test_recording_create_roundtrip_with_defaults(storage):
    user_id = storage.test_user_id
    row = await storage.recording_create(
        user_id, {"id": "rec1", "filename": "a.mp3", "ext": "mp3"},
    )
    assert await storage.recording_get(user_id, "rec1") == row
    assert row["status"] == "processing"
    assert row["asr_task_id"] is None and row["report_id"] is None


async def test_recording_update_whitelist(storage):
    user_id = storage.test_user_id
    await storage.recording_create(user_id, {"id": "rec1", "filename": "a.mp3", "ext": "mp3"})
    report = await storage.report_create(user_id, {
        "id": "rep1", "session_id": None, "scores_json": "{}",
        "feedback_json": "{}", "suggestions_json": "[]",
    })
    updated = await storage.recording_update(
        user_id, "rec1", {"status": "done", "report_id": report["id"], "unknown_col": "x"},
    )
    assert updated["status"] == "done" and updated["report_id"] == "rep1"
    assert "unknown_col" not in updated


async def test_recording_list_processing_filters_by_status(storage):
    user_id = storage.test_user_id
    await storage.recording_create(user_id, {"id": "a", "filename": "a.mp3", "ext": "mp3"})
    await storage.recording_create(
        user_id, {"id": "b", "filename": "b.mp3", "ext": "mp3", "status": "done"},
    )
    rows = await storage.recording_list_processing()
    assert [row["id"] for row in rows] == ["a"]


async def test_report_source_defaults_and_recording_source(storage):
    user_id = storage.test_user_id
    session_report = await storage.report_create(user_id, {
        "session_id": None, "scores_json": "{}", "feedback_json": "{}", "suggestions_json": "[]",
    })
    recording_report = await storage.report_create(user_id, {
        "session_id": None, "source": "recording", "scores_json": "{}",
        "feedback_json": "{}", "suggestions_json": "[]",
    })
    assert session_report["source"] == "session"
    assert (await storage.report_get(user_id, recording_report["id"]))["source"] == "recording"


async def test_init_migrates_legacy_db_without_source_column(tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(str(db_path))
    connection.execute(
        "CREATE TABLE interview_report (id TEXT PRIMARY KEY, session_id TEXT, scores_json TEXT, "
        "feedback_json TEXT, suggestions_json TEXT, md_path TEXT, created_at TEXT)"
    )
    connection.commit()
    connection.close()
    storage = SqliteStorage(str(db_path))
    await storage.init()
    await storage.init()
    user_id = (await storage.user_create("legacy-user", "hash"))["id"]
    report = await storage.report_create(user_id, {
        "session_id": None, "scores_json": "{}", "feedback_json": "{}",
        "suggestions_json": "[]", "source": "recording",
    })
    assert (await storage.report_get(user_id, report["id"]))["source"] == "recording"
    await storage.close()
