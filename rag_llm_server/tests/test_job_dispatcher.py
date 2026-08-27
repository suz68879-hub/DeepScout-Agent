import os
import uuid

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, func, select

from config import Config
from db.engine import build_database_runtime
from db.models import AppUser, BackgroundJob, OutboxEvent
from services.jobs.dispatcher import (
    DispatchErrorCode,
    InlineJobDispatcher,
    JobDispatchError,
    JobDispatcher,
)
from services.jobs.handlers import (
    JobHandlerError,
    JobHandlerRegistry,
    JobType,
)
from services.jobs.repository import JobRepository
from services.jobs.types import JobErrorCode, JobStatus


@pytest.fixture
async def dispatch_runtime(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for job dispatcher tests")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    runtime = build_database_runtime(Config())
    await runtime.start()

    owner_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add(
            AppUser(
                id=owner_id,
                username=f"dispatch_owner_{owner_id.hex}",
                password_hash="test-only",
            )
        )
    try:
        yield runtime, owner_id
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.id == owner_id))
        await runtime.close()


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"job_type": "unknown.job"}, DispatchErrorCode.UNKNOWN_JOB_TYPE),
        ({"job_type": []}, DispatchErrorCode.UNKNOWN_JOB_TYPE),
        ({"owner_id": None}, DispatchErrorCode.INVALID_OWNER),
        ({"owner_id": "not-a-uuid"}, DispatchErrorCode.INVALID_OWNER),
        ({"idempotency_key": ""}, DispatchErrorCode.INVALID_IDEMPOTENCY_KEY),
        (
            {"payload_ref": {"schema_version": 1, "access_token": "secret"}},
            DispatchErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "job_type": JobType.RECORDING_PROCESS,
                "payload_ref": {
                    "schema_version": 1,
                    "recording_id": str(uuid.uuid4()),
                    "tos_key": "x" * 5000,
                }
            },
            DispatchErrorCode.INVALID_PAYLOAD,
        ),
    ],
)
async def test_enqueue_rejects_invalid_boundary_input(overrides, expected_code):
    arguments = {
        "job_type": JobType.INTERVIEW_FINISH,
        "owner_id": uuid.uuid4(),
        "payload_ref": {"schema_version": 1, "session_id": str(uuid.uuid4())},
        "idempotency_key": "valid-key",
    }
    arguments.update(overrides)

    with pytest.raises(JobDispatchError) as captured:
        await JobDispatcher(None).enqueue(**arguments)

    assert captured.value.code is expected_code
    assert str(captured.value) == expected_code.value


async def test_enqueue_writes_job_and_single_outbox_in_caller_transaction(
    dispatch_runtime,
):
    runtime, owner_id = dispatch_runtime
    session_id = uuid.uuid4()

    async with runtime.session_factory() as session:
        dispatcher = JobDispatcher(session)
        first = await dispatcher.enqueue(
            job_type=JobType.INTERVIEW_FINISH,
            owner_id=owner_id,
            payload_ref={"schema_version": 1, "session_id": str(session_id)},
            idempotency_key="same-dispatch-key",
        )
        second = await dispatcher.enqueue(
            job_type=JobType.INTERVIEW_FINISH,
            owner_id=owner_id,
            payload_ref={"schema_version": 1, "session_id": str(session_id)},
            idempotency_key="same-dispatch-key",
        )
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == first.id)
        )
        event_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == first.id)
        )

        assert first.id == second.id
        assert first.status is JobStatus.PENDING
        assert event_count == 1
        assert event.aggregate_type == "background_job"
        assert event.event_type == "job.created"
        assert event.payload == {
            "schema_version": 1,
            "job_id": str(first.id),
            "job_type": JobType.INTERVIEW_FINISH.value,
        }
        await session.rollback()

    async with runtime.session_scope() as session:
        assert await JobRepository(session).get(owner_id, first.id) is None
        assert await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == first.id)
        ) == 0


async def test_enqueue_accepts_recording_reference_payload(dispatch_runtime):
    runtime, owner_id = dispatch_runtime

    async with runtime.session_scope() as session:
        job = await JobDispatcher(session).enqueue(
            job_type=JobType.RECORDING_PROCESS,
            owner_id=owner_id,
            payload_ref={
                "schema_version": 1,
                "recording_id": str(uuid.uuid4()),
                "tos_key": "recordings/test/sample.wav",
            },
            idempotency_key="recording-dispatch-key",
        )

    assert job.job_type == JobType.RECORDING_PROCESS.value


async def test_enqueue_maps_persistence_conflict_to_stable_error(dispatch_runtime):
    runtime, _ = dispatch_runtime

    async with runtime.session_scope() as session:
        with pytest.raises(JobDispatchError) as captured:
            await JobDispatcher(session).enqueue(
                job_type=JobType.INTERVIEW_FINISH,
                owner_id=uuid.uuid4(),
                payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
                idempotency_key="missing-owner-key",
            )

    assert captured.value.code is DispatchErrorCode.JOB_CONFLICT


def test_inline_adapter_is_forbidden_in_production():
    with pytest.raises(JobDispatchError) as captured:
        InlineJobDispatcher(
            None,
            JobHandlerRegistry({}),
            app_env="production",
        )

    assert captured.value.code is DispatchErrorCode.INLINE_FORBIDDEN


def test_inline_adapter_cannot_override_production_config(monkeypatch):
    monkeypatch.setattr(
        "services.jobs.dispatcher.settings.APP_ENV",
        "production",
    )

    with pytest.raises(JobDispatchError) as captured:
        InlineJobDispatcher(
            None,
            JobHandlerRegistry({}),
            app_env="test",
        )

    assert captured.value.code is DispatchErrorCode.INLINE_FORBIDDEN


async def test_inline_adapter_routes_handlers_through_state_machine(dispatch_runtime):
    runtime, owner_id = dispatch_runtime
    calls = []

    async def interview_handler(job):
        calls.append(job.job_type)
        return {"schema_version": 1, "report_id": str(uuid.uuid4())}

    async def recording_handler(job):
        calls.append(job.job_type)
        return {"schema_version": 1, "recording_id": str(uuid.uuid4())}

    handlers = JobHandlerRegistry(
        {
            JobType.INTERVIEW_FINISH: interview_handler,
            JobType.RECORDING_PROCESS: recording_handler,
        }
    )
    async with runtime.session_scope() as session:
        dispatcher = InlineJobDispatcher(session, handlers, app_env="test")
        interview = await dispatcher.enqueue(
            job_type=JobType.INTERVIEW_FINISH,
            owner_id=owner_id,
            payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
            idempotency_key="inline-interview-key",
        )
        repeated = await dispatcher.enqueue(
            job_type=JobType.INTERVIEW_FINISH,
            owner_id=owner_id,
            payload_ref=interview.payload_ref,
            idempotency_key="inline-interview-key",
        )
        recording = await dispatcher.enqueue(
            job_type=JobType.RECORDING_PROCESS,
            owner_id=owner_id,
            payload_ref={
                "schema_version": 1,
                "recording_id": str(uuid.uuid4()),
                "tos_key": "recordings/test/inline.wav",
            },
            idempotency_key="inline-recording-key",
        )

    assert interview.status is JobStatus.SUCCEEDED
    assert repeated.id == interview.id
    assert repeated.status is JobStatus.SUCCEEDED
    assert recording.status is JobStatus.SUCCEEDED
    assert calls == [
        JobType.INTERVIEW_FINISH.value,
        JobType.RECORDING_PROCESS.value,
    ]


@pytest.mark.parametrize(
    ("raised_error", "expected_code"),
    [
        (JobHandlerError(JobErrorCode.PROVIDER_ERROR), JobErrorCode.PROVIDER_ERROR),
        (RuntimeError("password=secret\ninternal stack"), JobErrorCode.INTERNAL_ERROR),
    ],
)
async def test_inline_adapter_maps_handler_exceptions_to_public_codes(
    dispatch_runtime,
    raised_error,
    expected_code,
):
    runtime, owner_id = dispatch_runtime

    async def failing_handler(job):
        del job
        raise raised_error

    handlers = JobHandlerRegistry({JobType.INTERVIEW_FINISH: failing_handler})
    async with runtime.session_scope() as session:
        failed = await InlineJobDispatcher(session, handlers, app_env="test").enqueue(
            job_type=JobType.INTERVIEW_FINISH,
            owner_id=owner_id,
            payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
            idempotency_key=f"inline-failure-{expected_code.value}",
        )

    assert failed.status is JobStatus.FAILED
    assert failed.error_code is expected_code


async def test_inline_adapter_rejects_missing_handler_without_committing(dispatch_runtime):
    runtime, owner_id = dispatch_runtime

    with pytest.raises(JobDispatchError) as captured:
        async with runtime.session_scope() as session:
            await InlineJobDispatcher(
                session,
                JobHandlerRegistry({}),
                app_env="test",
            ).enqueue(
                job_type=JobType.INTERVIEW_FINISH,
                owner_id=owner_id,
                payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
                idempotency_key="missing-handler-key",
            )

    assert captured.value.code is DispatchErrorCode.HANDLER_NOT_CONFIGURED
    async with runtime.session_scope() as session:
        assert await session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.idempotency_key == "missing-handler-key")
        ) == 0
