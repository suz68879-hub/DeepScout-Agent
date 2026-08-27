import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete

from config import Config
from db.engine import build_database_runtime
from db.models import AppUser, Recording
from services.jobs.repository import JobRepository
from services.jobs.types import JobErrorCode, JobStatus
from services.recording_service import RecordingModelOutputError, RecordingPollPending
from tasks.recording_tasks import (
    RecordingTaskMessageError,
    RecordingTaskPending,
    execute_recording_job,
)


@pytest.fixture
async def recording_task_runtime(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for recording task tests")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    runtime = build_database_runtime(Config())
    await runtime.start()
    owner_id = uuid.uuid4()
    recording_id = uuid.uuid4()
    tos_key = f"users/{owner_id}/recordings/{recording_id}.wav"
    async with runtime.session_scope() as session:
        session.add(
            AppUser(
                id=owner_id,
                username=f"recording_task_{owner_id.hex}",
                password_hash="test-only",
            )
        )
        session.add(
            Recording(
                id=recording_id,
                user_id=owner_id,
                filename="interview.wav",
                ext="wav",
                position="Java backend",
                tos_key=tos_key,
                status="processing",
            )
        )
    try:
        yield runtime, owner_id, recording_id, tos_key
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.id == owner_id))
        await runtime.close()


async def _create_job(runtime, owner_id, recording_id, tos_key, key, max_attempts=5):
    async with runtime.session_scope() as session:
        return await JobRepository(session).create(
            owner_id=owner_id,
            job_type="recording.process",
            payload_ref={
                "schema_version": 1,
                "recording_id": str(recording_id),
                "tos_key": tos_key,
            },
            idempotency_key=key,
            max_attempts=max_attempts,
        )


async def _execute(runtime, job, processor):
    return await execute_recording_job(
        runtime,
        schema_version=1,
        job_id=str(job.id),
        job_type="recording.process",
        processor=processor,
    )


async def test_duplicate_delivery_executes_recording_once(recording_task_runtime):
    runtime, owner_id, recording_id, tos_key = recording_task_runtime
    job = await _create_job(runtime, owner_id, recording_id, tos_key, "duplicate")
    calls = []

    async def processor(claimed):
        calls.append(claimed.id)
        return {
            "schema_version": 1,
            "recording_id": str(recording_id),
            "report_id": str(recording_id),
        }

    first = await _execute(runtime, job, processor)
    repeated = await _execute(runtime, job, processor)

    assert first == repeated
    assert first["status"] == JobStatus.SUCCEEDED.value
    assert calls == [job.id]


async def test_asr_pending_requeues_without_worker_sleep(recording_task_runtime):
    runtime, owner_id, recording_id, tos_key = recording_task_runtime
    job = await _create_job(runtime, owner_id, recording_id, tos_key, "pending")

    async def pending(_job):
        raise RecordingPollPending(str(recording_id))

    with pytest.raises(RecordingTaskPending) as exc_info:
        await _execute(runtime, job, pending)

    assert exc_info.value.countdown == 5

    async with runtime.session_scope() as session:
        persisted = await JobRepository(session).get_internal(job.id)
    assert persisted.status is JobStatus.PENDING
    assert persisted.attempt == 1


async def test_transient_tos_or_asr_network_failure_requeues(recording_task_runtime):
    runtime, owner_id, recording_id, tos_key = recording_task_runtime
    job = await _create_job(runtime, owner_id, recording_id, tos_key, "network")

    async def unavailable(_job):
        raise OSError("provider host is temporarily unavailable")

    with pytest.raises(RecordingTaskPending) as exc_info:
        await _execute(runtime, job, unavailable)

    assert exc_info.value.countdown == 5

    async with runtime.session_scope() as session:
        persisted = await JobRepository(session).get_internal(job.id)
    assert persisted.status is JobStatus.PENDING
    assert persisted.attempt == 1


async def test_asr_timeout_fails_at_persisted_attempt_limit(recording_task_runtime):
    runtime, owner_id, recording_id, tos_key = recording_task_runtime
    job = await _create_job(
        runtime, owner_id, recording_id, tos_key, "timeout", max_attempts=1
    )

    async def pending(_job):
        raise RecordingPollPending(str(recording_id))

    with pytest.raises(RecordingTaskPending):
        await _execute(runtime, job, pending)
    result = await _execute(runtime, job, pending)

    assert result["status"] == JobStatus.FAILED.value
    assert result["error_code"] == JobErrorCode.MAX_ATTEMPTS_EXCEEDED.value
    async with runtime.session_scope() as session:
        recording = await session.get(Recording, recording_id)
    assert recording.status == "failed"
    assert recording.error == "transcription timed out"


async def test_worker_restart_resumes_requeued_recording(recording_task_runtime):
    runtime, owner_id, recording_id, tos_key = recording_task_runtime
    job = await _create_job(runtime, owner_id, recording_id, tos_key, "restart")
    old = datetime.now(timezone.utc) - timedelta(seconds=10)
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        await repository.claim(job.id, lease_duration=timedelta(seconds=1), now=old)

    async def processor(_job):
        return {
            "schema_version": 1,
            "recording_id": str(recording_id),
            "report_id": str(recording_id),
        }

    with pytest.raises(RecordingTaskPending) as exc_info:
        await _execute(runtime, job, processor)
    assert exc_info.value.countdown == 5
    result = await _execute(runtime, job, processor)

    assert result["status"] == JobStatus.SUCCEEDED.value
    async with runtime.session_scope() as session:
        persisted = await JobRepository(session).get_internal(job.id)
    assert persisted.attempt == 1


async def test_worker_lost_at_retry_limit_fails_job_and_recording(
    recording_task_runtime,
):
    runtime, owner_id, recording_id, tos_key = recording_task_runtime
    job = await _create_job(
        runtime,
        owner_id,
        recording_id,
        tos_key,
        "worker-lost-limit",
        max_attempts=1,
    )
    old = datetime.now(timezone.utc) - timedelta(seconds=10)
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        await repository.claim(job.id, lease_duration=timedelta(seconds=1), now=old)
        await repository.recover_expired(job.id)
        await repository.claim(job.id, lease_duration=timedelta(seconds=1), now=old)

    async def must_not_run(_job):
        raise AssertionError("exhausted worker-lost job must not run")

    result = await _execute(runtime, job, must_not_run)

    assert result["status"] == JobStatus.FAILED.value
    assert result["error_code"] == JobErrorCode.MAX_ATTEMPTS_EXCEEDED.value
    async with runtime.session_scope() as session:
        recording = await session.get(Recording, recording_id)
    assert recording.status == "failed"
    assert recording.error == "recording worker retry limit exceeded"


async def test_model_output_failure_uses_public_error(recording_task_runtime):
    runtime, owner_id, recording_id, tos_key = recording_task_runtime
    job = await _create_job(runtime, owner_id, recording_id, tos_key, "model-output")

    async def failed(_job):
        raise RecordingModelOutputError("sensitive model output")

    result = await _execute(runtime, job, failed)

    assert result["status"] == JobStatus.FAILED.value
    assert result["error_code"] == JobErrorCode.PROVIDER_ERROR.value


async def test_permission_failure_is_terminal(recording_task_runtime):
    runtime, owner_id, recording_id, tos_key = recording_task_runtime
    job = await _create_job(runtime, owner_id, recording_id, tos_key, "permission")

    async def denied(_job):
        raise PermissionError("private path")

    result = await _execute(runtime, job, denied)

    assert result["status"] == JobStatus.FAILED.value
    assert result["error_code"] == JobErrorCode.PERMISSION_DENIED.value


@pytest.mark.parametrize(
    "message",
    [
        {"schema_version": True, "job_id": str(uuid.uuid4()), "job_type": "recording.process"},
        {"schema_version": 1, "job_id": "bad", "job_type": "recording.process"},
        {"schema_version": 1, "job_id": str(uuid.uuid4()), "job_type": "unknown"},
    ],
)
async def test_recording_worker_rejects_untrusted_message(message):
    with pytest.raises(RecordingTaskMessageError):
        await execute_recording_job(None, **message)


def test_recording_celery_task_is_registered():
    import tasks.recording_tasks  # noqa: F401
    from tasks.celery_app import celery_app

    assert "tasks.recording_tasks.process_recording" in celery_app.tasks


def test_terminal_recording_job_publishes_privacy_safe_dead_letter(monkeypatch):
    import tasks.recording_tasks as recording_tasks

    job_id = uuid.uuid4()
    published = []

    async def failed_job(*_args, **_kwargs):
        return {
            "job_id": str(job_id),
            "status": JobStatus.FAILED.value,
            "error_code": JobErrorCode.MAX_ATTEMPTS_EXCEEDED.value,
        }

    class FakePublisher:
        def __init__(self, _app):
            pass

        def publish(self, **message):
            published.append(message)

    monkeypatch.setattr(recording_tasks, "run_recording_job", failed_job)
    monkeypatch.setattr(recording_tasks, "DeadLetterPublisher", FakePublisher)

    result = recording_tasks.process_recording.run(
        1,
        str(job_id),
        "recording.process",
    )

    assert result["status"] == JobStatus.FAILED.value
    assert published == [
        {
            "job_id": str(job_id),
            "error_code": JobErrorCode.MAX_ATTEMPTS_EXCEEDED.value,
            "original_queue": "deepscout.recording",
        }
    ]


async def test_recording_worker_lifecycle_always_closes_resources(monkeypatch):
    import tasks.recording_tasks as recording_tasks

    events = []

    async def record(name):
        events.append(name)

    monkeypatch.setattr(recording_tasks, "init_database", lambda _config: record("init_database"))
    monkeypatch.setattr(recording_tasks, "init_storage", lambda: record("init_storage"))
    monkeypatch.setattr(recording_tasks, "get_database_runtime", lambda: object())
    monkeypatch.setattr(recording_tasks, "execute_recording_job", lambda *_args, **_kwargs: record("execute"))
    monkeypatch.setattr(recording_tasks, "close_storage", lambda: record("close_storage"))
    monkeypatch.setattr(recording_tasks, "close_database", lambda: record("close_database"))

    await recording_tasks.run_recording_job(
        Config(),
        schema_version=1,
        job_id=str(uuid.uuid4()),
        job_type="recording.process",
    )

    assert events == [
        "init_database",
        "init_storage",
        "execute",
        "close_storage",
        "close_database",
    ]
