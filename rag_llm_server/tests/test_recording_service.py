"""Recording pipeline orchestration tests with external services stubbed."""
import pytest

from agents.recording_analyzer import SpeakerAssignment
from agents.reporter import Report
from services import recording_service as service
from services.asr_client import AsrError


USER_ID = "u1"


class FakeTosStore:
    def __init__(self, fail=False):
        self.saved = {}
        self.fail = fail

    async def save_bytes(self, key, content):
        if self.fail:
            raise OSError("upload failed")
        self.saved[key] = content
        return key

    def presigned_url(self, key, expires=3600):
        return f"https://fake.tos/{key}"


class FakeDb:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.created = []
        self.updates = []

    async def recording_create(self, user_id, row):
        value = {**row, "user_id": user_id}
        self.created.append(value.copy())
        self.rows[row["id"]] = value
        return value

    async def recording_get(self, user_id, recording_id):
        row = self.rows.get(recording_id)
        return row if row and row["user_id"] == user_id else None

    async def recording_get_internal(self, recording_id):
        return self.rows.get(recording_id)

    async def recording_update(self, user_id, recording_id, patch):
        self.updates.append((user_id, recording_id, dict(patch)))
        row = self.rows.setdefault(recording_id, {"id": recording_id, "user_id": user_id})
        row.update(patch)
        return row

    async def recording_list_processing(self):
        return [row for row in self.rows.values() if row.get("status") == "processing"]


def processing_row(recording_id="rec1", task_id="t-1"):
    return {
        "id": recording_id,
        "user_id": USER_ID,
        "status": "processing",
        "asr_task_id": task_id,
        "filename": "a.mp3",
        "ext": "mp3",
        "tos_key": f"users/{USER_ID}/recordings/{recording_id}.mp3",
    }


async def test_upload_recording_requires_tos(monkeypatch):
    database = FakeDb()
    monkeypatch.setattr(service, "storage", database)
    monkeypatch.setattr(service, "get_tos_store", lambda: None)
    with pytest.raises(RuntimeError):
        await service.upload_recording(USER_ID, "a.mp3", "mp3", b"x", "Java backend")
    assert database.created == []


async def test_upload_recording_tos_failure_no_row(monkeypatch):
    database = FakeDb()
    monkeypatch.setattr(service, "storage", database)
    monkeypatch.setattr(service, "get_tos_store", lambda: FakeTosStore(fail=True))
    with pytest.raises(OSError):
        await service.upload_recording(USER_ID, "a.mp3", "mp3", b"x", "Java backend")
    assert database.created == []


async def test_upload_recording_submit_failure_marks_failed(monkeypatch):
    database = FakeDb()
    monkeypatch.setattr(service, "storage", database)
    monkeypatch.setattr(service, "get_tos_store", lambda: FakeTosStore())

    async def fail_submit(url, ext):
        raise AsrError("45000001", "invalid request")

    monkeypatch.setattr(service, "submit_asr", fail_submit)
    with pytest.raises(AsrError):
        await service.upload_recording(USER_ID, "a.mp3", "mp3", b"x", "Java backend")
    assert database.updates[-1][2]["status"] == "failed"
    assert database.updates[-1][2]["error"] == "speech recognition task submission failed"


async def test_upload_recording_schedules_worker(monkeypatch):
    database = FakeDb()
    monkeypatch.setattr(service, "storage", database)
    monkeypatch.setattr(service, "get_tos_store", lambda: FakeTosStore())

    async def submit(url, ext):
        return "t-1"

    scheduled = []
    monkeypatch.setattr(service, "submit_asr", submit)
    monkeypatch.setattr(service, "schedule_recording", lambda recording_id, position: scheduled.append(recording_id))
    row = await service.upload_recording(USER_ID, "a.mp3", "mp3", b"x", "Java backend")
    assert row["asr_task_id"] == "t-1"
    assert scheduled == [row["id"]]
    assert row["tos_key"].startswith(f"users/{USER_ID}/recordings/")


async def test_schedule_recording_is_idempotent(monkeypatch):
    scheduled = []
    monkeypatch.setattr(service, "create_task", lambda coroutine: scheduled.append(coroutine) or coroutine.close())
    monkeypatch.setattr(service, "_running_tasks", set())
    service.schedule_recording("rec1")
    service.schedule_recording("rec1")
    assert len(scheduled) == 1


async def test_process_recording_done_path(monkeypatch):
    database = FakeDb({"rec1": processing_row()})
    monkeypatch.setattr(service, "storage", database)
    calls = {"count": 0}

    async def query(task_id):
        calls["count"] += 1
        if calls["count"] < 2:
            return None
        return {"result": {"text": "all", "utterances": [{
            "text": "answer", "start_time": 0, "end_time": 100, "additions": {"speaker": "1"},
        }]}}

    async def judge(transcript):
        return SpeakerAssignment(candidate_speaker="1", confidence="high", reason="")

    async def generate(segments, position):
        return Report(
            summary="good", strengths=["s1", "s2"], improvements=["i1", "i2"], suggestions=["p1"],
        )

    async def save(*args):
        return "rep-1"

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(service, "query_asr", query)
    monkeypatch.setattr(service, "judge_roles", judge)
    monkeypatch.setattr(service, "generate_recording_report", generate)
    monkeypatch.setattr(service, "save_recording_report", save)
    monkeypatch.setattr(service, "sleep", no_sleep)
    await service._process_recording("rec1", "Java backend")
    assert database.updates[-1][2]["status"] == "done"
    assert database.updates[-1][2]["report_id"] == "rep-1"


async def test_process_recording_timeout_marks_failed(monkeypatch):
    database = FakeDb({"rec1": processing_row()})
    monkeypatch.setattr(service, "storage", database)
    monkeypatch.setattr(service, "POLL_TIMEOUT_SECONDS", -1)

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(service, "sleep", no_sleep)
    await service._process_recording("rec1", "Java backend")
    assert database.updates[-1][2]["error"] == "transcription timed out"


async def test_process_recording_analysis_failure_hides_details(monkeypatch):
    database = FakeDb({"rec1": processing_row()})
    monkeypatch.setattr(service, "storage", database)

    async def no_sleep(seconds):
        return None

    async def query(task_id):
        return {"result": {"text": "all", "utterances": [{
            "text": "answer", "start_time": 0, "end_time": 100, "additions": {"speaker": "1"},
        }]}}

    async def fail_judge(transcript):
        raise RuntimeError("sensitive model response")

    monkeypatch.setattr(service, "sleep", no_sleep)
    monkeypatch.setattr(service, "query_asr", query)
    monkeypatch.setattr(service, "judge_roles", fail_judge)
    await service._process_recording("rec1", "Java backend")
    assert database.updates[-1][2]["error"] == "recording analysis failed"
    assert "answer" in database.updates[-1][2]["transcript_json"]


async def test_resume_pending_keeps_original_owner(monkeypatch):
    database = FakeDb({
        "r1": processing_row("r1", "t-1"),
        "r2": processing_row("r2", None),
    })
    monkeypatch.setattr(service, "storage", database)
    scheduled = []
    monkeypatch.setattr(service, "schedule_recording", lambda recording_id: scheduled.append(recording_id))
    resumed = await service.resume_pending()
    assert resumed == ["r1"]
    assert scheduled == ["r1"]
    assert database.updates[-1][:2] == (USER_ID, "r2")
