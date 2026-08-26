"""Recording pipeline orchestration tests with external services stubbed."""
import inspect
import os
import uuid

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, func, select

from agents.recording_analyzer import SpeakerAssignment
from agents.reporter import Report
from config import Config
from db.engine import build_database_runtime
from db.models import AppUser, BackgroundJob, OutboxEvent, Recording
from services import recording_service as service
USER_ID = "11111111-1111-1111-1111-111111111111"
RECORDING_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
async def recording_job_runtime(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for recording job tests")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    runtime = build_database_runtime(Config())
    await runtime.start()
    owner_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add(
            AppUser(
                id=owner_id,
                username=f"recording_job_{owner_id.hex}",
                password_hash="test-only",
            )
        )
    try:
        yield runtime, owner_id
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.id == owner_id))
        await runtime.close()


async def test_create_recording_job_writes_recording_and_outbox_atomically(
    recording_job_runtime,
):
    runtime, owner_id = recording_job_runtime
    recording_id = uuid.uuid4()
    tos_key = f"users/{owner_id}/recordings/{recording_id}.wav"

    async with runtime.session_scope() as session:
        row, job = await service.create_recording_job(
            session,
            owner_id=owner_id,
            recording={
                "id": str(recording_id),
                "filename": "interview.wav",
                "ext": "wav",
                "tos_key": tos_key,
                "size_bytes": 4,
                "position": "Java 后端",
                "status": "processing",
            },
        )

    assert row["position"] == "Java 后端"
    assert job.payload_ref == {
        "schema_version": 1,
        "recording_id": str(recording_id),
        "tos_key": tos_key,
    }
    async with runtime.session_scope() as session:
        assert await session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.id == job.id)
        ) == 1
        assert await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == job.id)
        ) == 1
        persisted = await session.get(Recording, recording_id)
    assert persisted.position == "Java 后端"


def test_recording_service_has_no_process_local_task_registry():
    assert not hasattr(service, "_running_tasks")
    assert not hasattr(service, "resume_pending")
    assert not hasattr(service, "schedule_recording")
    assert "create_task" not in inspect.getsource(service)


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
        self.reports = {}
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

    async def report_get(self, user_id, report_id):
        row = self.reports.get(report_id)
        return row if row and row["user_id"] == user_id else None


def processing_row(recording_id=RECORDING_ID, task_id="t-1"):
    return {
        "id": recording_id,
        "user_id": USER_ID,
        "status": "processing",
        "asr_task_id": task_id,
        "filename": "a.mp3",
        "ext": "mp3",
        "position": "Java backend",
        "tos_key": f"users/{USER_ID}/recordings/{recording_id}.mp3",
    }


def recording_job(recording_id=RECORDING_ID):
    return type("Job", (), {
        "id": uuid.uuid4(),
        "owner_id": USER_ID,
        "job_type": "recording.process",
        "payload_ref": {
            "schema_version": 1,
            "recording_id": recording_id,
            "tos_key": f"users/{USER_ID}/recordings/{recording_id}.mp3",
        },
    })()


async def test_upload_persists_job_without_calling_asr_in_api_process(monkeypatch):
    store = FakeTosStore()
    captured = {}

    async def enqueue(owner_id, recording):
        captured.update({"owner_id": owner_id, "recording": recording})
        return {**recording, "user_id": owner_id}, type(
            "Job", (), {"id": uuid.uuid4()}
        )()

    monkeypatch.setattr(service, "get_tos_store", lambda: store)
    monkeypatch.setattr(service, "enqueue_uploaded_recording", enqueue, raising=False)

    result = await service.upload_recording(
        USER_ID, "a.mp3", "mp3", b"audio", "Java backend"
    )

    assert result["job_id"]
    assert captured["recording"]["position"] == "Java backend"
    assert captured["recording"]["asr_task_id"] is None
    assert store.saved[captured["recording"]["tos_key"]] == b"audio"


async def test_process_recording_pending_queries_once_without_sleep(monkeypatch):
    database = FakeDb({RECORDING_ID: processing_row(task_id=None)})
    store = FakeTosStore()
    submitted = []
    queried = []

    async def submit(url, ext, *, task_id=None):
        submitted.append((url, ext, task_id))
        return task_id

    async def query(task_id):
        queried.append(task_id)
        return None

    monkeypatch.setattr(service, "storage", database)
    monkeypatch.setattr(service, "get_tos_store", lambda: store)
    monkeypatch.setattr(service, "submit_asr", submit)
    monkeypatch.setattr(service, "query_asr", query)

    with pytest.raises(service.RecordingPollPending):
        await service.process_recording(recording_job())

    assert len(submitted) == 1
    assert submitted[0][2] == database.rows[RECORDING_ID]["asr_task_id"]
    assert queried == [database.rows[RECORDING_ID]["asr_task_id"]]


async def test_existing_report_repairs_recording_without_duplicate_analysis(monkeypatch):
    database = FakeDb({RECORDING_ID: processing_row()})
    database.reports[RECORDING_ID] = {
        "id": RECORDING_ID,
        "user_id": USER_ID,
        "source": "recording",
    }
    monkeypatch.setattr(service, "storage", database)

    result = await service.process_recording(recording_job())

    assert result["report_id"] == RECORDING_ID
    assert database.updates[-1][2]["status"] == "done"
    assert database.updates[-1][2]["report_id"] == RECORDING_ID


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


async def test_process_recording_done_path(monkeypatch):
    database = FakeDb({RECORDING_ID: processing_row()})
    monkeypatch.setattr(service, "storage", database)

    async def query(task_id):
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
        return RECORDING_ID

    monkeypatch.setattr(service, "query_asr", query)
    monkeypatch.setattr(service, "judge_roles", judge)
    monkeypatch.setattr(service, "generate_recording_report", generate)
    monkeypatch.setattr(service, "save_recording_report", save)
    result = await service.process_recording(recording_job())
    assert database.updates[-1][2]["status"] == "done"
    assert database.updates[-1][2]["report_id"] == RECORDING_ID
    assert result["report_id"] == RECORDING_ID
