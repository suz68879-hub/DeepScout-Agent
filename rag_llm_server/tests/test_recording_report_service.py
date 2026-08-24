"""Recording report ownership and persistence tests."""
import json

from services.report_service import save_recording_report


async def test_save_recording_report(tmp_path, monkeypatch):
    from services import report_service
    from services.storage.file_storage import LocalFileStorage
    from services.storage.sqlite import SqliteStorage

    storage = SqliteStorage(str(tmp_path / "recording.db"))
    await storage.init()
    user_id = (await storage.user_create("recording-user", "hash"))["id"]
    await storage.recording_create(
        user_id, {"id": "rec1", "filename": "a.wav", "status": "processing"},
    )
    monkeypatch.setattr(report_service, "storage", storage)
    monkeypatch.setattr(
        report_service, "file_store", LocalFileStorage(str(tmp_path / "reports")),
    )

    report = {
        "summary": "good performance",
        "dimension_scores": {
            "technical_depth": 8.0,
            "project_understanding": 7.0,
            "communication": 8.5,
            "presence": 7.5,
        },
        "overall_score": 7.8,
        "round_details": [{
            "round_no": 1,
            "question": "Introduction",
            "answer_summary": "key points",
            "comment": "clear",
        }],
        "strengths": ["s1", "s2"],
        "improvements": ["i1", "i2"],
        "suggestions": ["practice"],
    }
    transcript = [{
        "speaker": "1", "role": "candidate", "start_ms": 0, "end_ms": 100, "text": "answer",
    }]
    assignment = {"candidate_speaker": "1", "confidence": "high", "reason": "long answers"}
    report_id = await save_recording_report(
        "rec1", report, "Java backend", transcript, assignment,
    )
    row = await storage.report_get(user_id, report_id)
    feedback = json.loads(row["feedback_json"])
    assert row["session_id"] is None
    assert row["source"] == "recording"
    assert row["position"] == "Java backend"
    assert feedback["transcript"] == transcript
    assert feedback["speaker_assignment"] == assignment
    assert feedback["round_scores"] == []
    await storage.close()
