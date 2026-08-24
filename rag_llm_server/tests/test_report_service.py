"""Report persistence and rendering tests."""
import json

import pytest

from services.report_service import save_report


async def make_context(tmp_path, monkeypatch, name):
    from services import report_service
    from services.storage.file_storage import LocalFileStorage
    from services.storage.sqlite import SqliteStorage

    storage = SqliteStorage(str(tmp_path / f"{name}.db"))
    await storage.init()
    user_id = (await storage.user_create(f"{name}-user", "hash"))["id"]
    session = await storage.session_create(user_id, {
        "id": name,
        "resume_id": None,
        "position": "Java backend",
        "stage": "finish",
        "status": "finished",
    })
    monkeypatch.setattr(report_service, "storage", storage)
    monkeypatch.setattr(
        report_service, "file_store", LocalFileStorage(str(tmp_path / f"{name}-reports")),
    )
    return storage, user_id, session


def report_payload():
    from agents.reporter import DIMENSIONS

    return {
        "summary": "good performance",
        "dimension_scores": {dimension: 7.0 for dimension in DIMENSIONS},
        "overall_score": 7.0,
        "round_details": [{
            "round_no": 1,
            "question": "Explain JVM",
            "answer_summary": "covered memory areas",
            "comment": "good",
        }],
        "strengths": ["s1", "s2"],
        "improvements": ["i1", "i2"],
        "suggestions": ["practice"],
    }


async def test_save_report_renders_md(tmp_path, monkeypatch):
    storage, user_id, session = await make_context(tmp_path, monkeypatch, "s1")
    report_id = await save_report(session, report_payload(), {"position": "Java backend"})
    row = await storage.report_get(user_id, report_id)
    with open(row["md_path"], encoding="utf-8") as report_file:
        markdown = report_file.read()
    assert row["session_id"] == "s1"
    assert "7.0/10" in markdown
    assert "Explain JVM" in markdown
    await storage.close()


async def test_save_report_persists_round_scores_and_position(tmp_path, monkeypatch):
    storage, user_id, session = await make_context(tmp_path, monkeypatch, "s2")
    state = {
        "position": "Java backend",
        "scores": [
            {"overall_score": 6.5, "dimensions": {}},
            {"status": "failed"},
            {"overall_score": 8.0, "dimensions": {}},
        ],
    }
    report_id = await save_report(session, report_payload(), state)
    row = await storage.report_get(user_id, report_id)
    assert json.loads(row["feedback_json"])["round_scores"] == [6.5, 8.0]
    assert row["position"] == "Java backend"
    await storage.close()
