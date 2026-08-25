import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, func, select

from agents.graph import ColdPathOutputError
from config import Config
from db.engine import build_database_runtime
from db.models import AppUser, BackgroundJob, InterviewReport, InterviewSession
from services.jobs.repository import JobRepository
from services.jobs.types import JobErrorCode, JobStatus
from tasks.interview_tasks import (
    InterviewTaskMessageError,
    PreviousInterviewJobPending,
    execute_interview_job,
)


@pytest.fixture
async def interview_task_runtime(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for interview task tests")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    runtime = build_database_runtime(Config())
    await runtime.start()
    owner_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add(
            AppUser(
                id=owner_id,
                username=f"interview_task_{owner_id.hex}",
                password_hash="test-only",
            )
        )
        session.add(
            InterviewSession(
                id=session_id,
                user_id=owner_id,
                position="Java后端",
                stage="technical",
                status="running",
                rtc_room_id=f"room-{session_id}",
                rtc_user_id=f"user-{session_id}",
                rtc_task_id=f"task-{session_id}",
                rtc_callback_id=f"callback-{session_id}",
            )
        )
    try:
        yield runtime, owner_id, session_id
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.id == owner_id))
        await runtime.close()


async def _create_job(runtime, owner_id, session_id, key):
    async with runtime.session_scope() as session:
        return await JobRepository(session).create(
            owner_id=owner_id,
            job_type="interview.finish",
            payload_ref={
                "schema_version": 1,
                "session_id": str(session_id),
                "step": "round",
            },
            idempotency_key=key,
        )


async def _execute(runtime, job, runner):
    return await execute_interview_job(
        runtime,
        schema_version=1,
        job_id=str(job.id),
        job_type="interview.finish",
        cold_runner=runner,
    )


async def test_duplicate_delivery_executes_cold_path_once(interview_task_runtime):
    runtime, owner_id, session_id = interview_task_runtime
    job = await _create_job(runtime, owner_id, session_id, "duplicate")
    calls = []

    async def runner(_job):
        calls.append(_job.id)
        return {"schema_version": 1, "session_id": str(session_id)}

    first = await _execute(runtime, job, runner)
    repeated = await _execute(runtime, job, runner)

    assert first == repeated
    assert first["status"] == JobStatus.SUCCEEDED.value
    assert calls == [job.id]


async def test_later_job_waits_while_prior_session_job_is_unfinished(
    interview_task_runtime,
):
    runtime, owner_id, session_id = interview_task_runtime
    await _create_job(runtime, owner_id, session_id, "round-1")
    later = await _create_job(runtime, owner_id, session_id, "round-2")

    with pytest.raises(PreviousInterviewJobPending):
        await _execute(runtime, later, lambda _job: None)

    async with runtime.session_scope() as session:
        persisted = await JobRepository(session).get_internal(later.id)
    assert persisted.status is JobStatus.PENDING


async def test_worker_restart_resumes_requeued_job(interview_task_runtime):
    runtime, owner_id, session_id = interview_task_runtime
    job = await _create_job(runtime, owner_id, session_id, "restart")
    old = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        await repository.claim(job.id, lease_duration=timedelta(seconds=1), now=old)
        await repository.requeue_expired(now=old + timedelta(seconds=2))

    async def runner(_job):
        return {"schema_version": 1, "session_id": str(session_id)}

    result = await _execute(runtime, job, runner)

    assert result["status"] == JobStatus.SUCCEEDED.value
    async with runtime.session_scope() as session:
        persisted = await JobRepository(session).get_internal(job.id)
    assert persisted.attempt == 1


async def test_existing_report_completes_restarted_job_without_duplicate(
    interview_task_runtime,
):
    runtime, owner_id, session_id = interview_task_runtime
    job = await _create_job(runtime, owner_id, session_id, "report-restart")
    report_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add(
            InterviewReport(
                id=report_id,
                user_id=owner_id,
                session_id=session_id,
                scores_json={},
                feedback_json={},
                suggestions_json=[],
                position="Java后端",
                source="session",
            )
        )

    async def must_not_run(_job):
        raise AssertionError("existing report must finish the job idempotently")

    result = await _execute(runtime, job, must_not_run)

    assert result["result_ref"]["report_id"] == str(report_id)
    async with runtime.session_scope() as session:
        assert await session.scalar(
            select(func.count())
            .select_from(InterviewReport)
            .where(InterviewReport.session_id == session_id)
        ) == 1
        interview = await session.get(InterviewSession, session_id)
    assert interview.status == "finished"


async def test_non_retryable_model_output_marks_job_failed(interview_task_runtime):
    runtime, owner_id, session_id = interview_task_runtime
    job = await _create_job(runtime, owner_id, session_id, "invalid-output")

    async def failed_runner(_job):
        raise ColdPathOutputError("EVALUATOR_OUTPUT_INVALID")

    result = await _execute(runtime, job, failed_runner)

    assert result == {
        "job_id": str(job.id),
        "status": JobStatus.FAILED.value,
        "error_code": JobErrorCode.PROVIDER_ERROR.value,
    }
    async with runtime.session_scope() as session:
        persisted = await JobRepository(session).get_internal(job.id)
    assert persisted.error_code is JobErrorCode.PROVIDER_ERROR


@pytest.mark.parametrize(
    "message",
    [
        {"schema_version": True, "job_id": str(uuid.uuid4()), "job_type": "interview.finish"},
        {"schema_version": 1, "job_id": "not-a-uuid", "job_type": "interview.finish"},
        {"schema_version": 1, "job_id": str(uuid.uuid4()), "job_type": "unknown"},
    ],
)
async def test_worker_rejects_untrusted_message_before_database(message):
    with pytest.raises(InterviewTaskMessageError, match="INVALID_INTERVIEW_TASK_MESSAGE"):
        await execute_interview_job(None, **message)


async def test_invalid_runner_result_is_persisted_as_failed(interview_task_runtime):
    runtime, owner_id, session_id = interview_task_runtime
    job = await _create_job(runtime, owner_id, session_id, "invalid-result")

    async def invalid_runner(_job):
        return {"schema_version": 1, "session_id": str(session_id), "secret": "x"}

    result = await _execute(runtime, job, invalid_runner)

    assert result["status"] == JobStatus.FAILED.value
    assert result["error_code"] == JobErrorCode.INVALID_INPUT.value


def test_interview_celery_task_is_registered():
    import tasks.interview_tasks  # noqa: F401
    from tasks.celery_app import celery_app

    assert "tasks.interview_tasks.finish_interview" in celery_app.tasks


async def test_worker_lifecycle_closes_remaining_resources_when_graph_close_fails(
    monkeypatch,
):
    import tasks.interview_tasks as interview_tasks

    events = []

    async def record(name):
        events.append(name)

    async def close_graph_failure():
        events.append("close_graph")
        raise RuntimeError("graph close failed")

    monkeypatch.setattr(interview_tasks, "init_database", lambda _config: record("init_database"))
    monkeypatch.setattr(interview_tasks, "init_storage", lambda: record("init_storage"))
    monkeypatch.setattr(interview_tasks, "init_graph", lambda: record("init_graph"))
    monkeypatch.setattr(interview_tasks, "get_database_runtime", lambda: object())
    monkeypatch.setattr(
        interview_tasks,
        "execute_interview_job",
        lambda *_args, **_kwargs: record("execute"),
    )
    monkeypatch.setattr(interview_tasks, "close_graph", close_graph_failure)
    monkeypatch.setattr(interview_tasks, "close_storage", lambda: record("close_storage"))
    monkeypatch.setattr(interview_tasks, "close_database", lambda: record("close_database"))

    with pytest.raises(RuntimeError, match="graph close failed"):
        await interview_tasks.run_interview_job(
            Config(),
            schema_version=1,
            job_id=str(uuid.uuid4()),
            job_type="interview.finish",
        )

    assert events == [
        "init_database",
        "init_storage",
        "init_graph",
        "execute",
        "close_graph",
        "close_storage",
        "close_database",
    ]
