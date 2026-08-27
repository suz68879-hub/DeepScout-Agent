import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, select
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from config import Config
from db.engine import build_database_runtime
from db.models import (
    AppUser,
    BackgroundJob,
    InterviewReport,
    InterviewSession,
    OutboxEvent,
    Recording,
)
from db.models import Base
from services.jobs.repository import JobRepository
from services.jobs.types import JobErrorCode, JobStatus
from scripts.replay_job import (
    ReplayCliError,
    replay_output,
    validate_execution_guard,
)
from services.jobs.replay import (
    JobReplayService,
    ReplayError,
    ReplayErrorCode,
    validate_replay_authorization,
)


@pytest.fixture
async def replay_runtime(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for job replay tests")
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
                username=f"job_replay_{owner_id.hex}",
                password_hash="test-only",
            )
        )
        session.add(
            InterviewSession(
                id=session_id,
                user_id=owner_id,
                position="Backend engineer",
                stage="finish",
                status="finished",
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


async def _failed_interview_job(runtime, owner_id, session_id, key):
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        job = await repository.create(
            owner_id=owner_id,
            job_type="interview.finish",
            payload_ref={
                "schema_version": 1,
                "session_id": str(session_id),
                "step": "finish",
            },
            idempotency_key=key,
        )
        await repository.claim(job.id, lease_duration=timedelta(minutes=1))
        return await repository.fail(job.id, JobErrorCode.PROVIDER_ERROR)


@pytest.mark.parametrize(
    ("operator", "reason", "error_code"),
    [
        ("", "approved incident recovery", ReplayErrorCode.INVALID_OPERATOR),
        ("ab", "approved incident recovery", ReplayErrorCode.INVALID_OPERATOR),
        ("operator\nforged", "approved incident recovery", ReplayErrorCode.INVALID_OPERATOR),
        ("operator@example.com", "short", ReplayErrorCode.INVALID_REASON),
        (
            "operator@example.com",
            "approved\nforged reason",
            ReplayErrorCode.INVALID_REASON,
        ),
    ],
)
def test_replay_authorization_rejects_missing_or_log_forging_input(
    operator,
    reason,
    error_code,
):
    with pytest.raises(ReplayError) as exc_info:
        validate_replay_authorization(
            operator=operator,
            reason=reason,
            approved_by=None,
            app_env="test",
        )

    assert exc_info.value.code is error_code


def test_production_replay_requires_distinct_second_approver():
    with pytest.raises(ReplayError) as missing:
        validate_replay_authorization(
            operator="operator@example.com",
            reason="approved incident recovery",
            approved_by=None,
            app_env="production",
        )
    assert missing.value.code is ReplayErrorCode.APPROVAL_REQUIRED

    with pytest.raises(ReplayError) as same_person:
        validate_replay_authorization(
            operator="operator@example.com",
            reason="approved incident recovery",
            approved_by="operator@example.com",
            app_env="production",
        )
    assert same_person.value.code is ReplayErrorCode.APPROVAL_REQUIRED

    authorized = validate_replay_authorization(
        operator="operator@example.com",
        reason="approved incident recovery",
        approved_by="approver@example.com",
        app_env="production",
    )
    assert authorized.approved_by == "approver@example.com"


def test_nonproduction_replay_normalizes_audit_identity_and_reason():
    authorization = validate_replay_authorization(
        operator=" operator@example.com ",
        reason=" approved incident recovery ",
        approved_by=None,
        app_env="test",
    )

    assert authorization.operator == "operator@example.com"
    assert authorization.reason == "approved incident recovery"
    assert authorization.approved_by is None


def test_background_job_schema_has_constrained_replay_audit_chain():
    table = Base.metadata.tables["background_job"]

    assert {
        "replay_of",
        "replay_operator",
        "replay_approved_by",
        "replay_reason",
        "replayed_at",
    } <= set(table.c.keys())
    assert table.c.replay_of.nullable is True
    assert table.c.replayed_at.type.timezone is True

    foreign_keys = {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert tuple(
        element.target_fullname
        for element in foreign_keys["fk_background_job_replay_of"].elements
    ) == ("background_job.id",)

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("replay_of",) in unique_columns

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    audit_check = checks["ck_background_job_replay_audit_complete"]
    assert all(
        field in audit_check
        for field in ("replay_of", "replay_operator", "replay_reason", "replayed_at")
    )


async def test_replay_creates_new_pending_job_outbox_and_audit_chain(replay_runtime):
    runtime, owner_id, session_id = replay_runtime
    source = await _failed_interview_job(
        runtime,
        owner_id,
        session_id,
        "replay-source",
    )

    async with runtime.session_scope() as session:
        outcome = await JobReplayService(session, app_env="test").replay(
            job_id=source.id,
            operator="operator@example.com",
            reason="approved provider recovery",
            approved_by=None,
            dry_run=False,
        )

    assert outcome.dry_run is False
    assert outcome.replay_job is not None
    assert outcome.replay_job.id != source.id
    assert outcome.replay_job.status is JobStatus.PENDING
    assert outcome.replay_job.owner_id == source.owner_id
    assert outcome.replay_job.payload_ref == source.payload_ref
    assert outcome.replay_job.replay_of == source.id
    assert outcome.replay_job.replay_operator == "operator@example.com"
    assert outcome.replay_job.replay_reason == "approved provider recovery"
    assert outcome.replay_job.replayed_at is not None
    async with runtime.session_scope() as session:
        persisted_source = await JobRepository(session).get_internal(source.id)
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == outcome.replay_job.id
            )
        )
    assert persisted_source.status is JobStatus.FAILED
    assert event is not None


async def test_dry_run_validates_without_writing_job_or_outbox(replay_runtime):
    runtime, owner_id, session_id = replay_runtime
    source = await _failed_interview_job(runtime, owner_id, session_id, "dry-run")
    async with runtime.session_scope() as session:
        before_jobs = len((await session.scalars(select(BackgroundJob.id))).all())
        before_events = len((await session.scalars(select(OutboxEvent.id))).all())
        outcome = await JobReplayService(session, app_env="test").replay(
            job_id=source.id,
            operator="operator@example.com",
            reason="validate recovery without writes",
            approved_by=None,
            dry_run=True,
        )
        after_jobs = len((await session.scalars(select(BackgroundJob.id))).all())
        after_events = len((await session.scalars(select(OutboxEvent.id))).all())

    assert outcome.dry_run is True
    assert outcome.replay_job is None
    assert (after_jobs, after_events) == (before_jobs, before_events)


async def test_duplicate_direct_replay_is_rejected(replay_runtime):
    runtime, owner_id, session_id = replay_runtime
    source = await _failed_interview_job(runtime, owner_id, session_id, "duplicate")
    async with runtime.session_scope() as session:
        service = JobReplayService(session, app_env="test")
        await service.replay(
            job_id=source.id,
            operator="operator@example.com",
            reason="first approved recovery",
            approved_by=None,
            dry_run=False,
        )
    async with runtime.session_scope() as session:
        with pytest.raises(ReplayError) as exc_info:
            await JobReplayService(session, app_env="test").replay(
                job_id=source.id,
                operator="operator@example.com",
                reason="duplicate approved recovery",
                approved_by=None,
                dry_run=False,
            )
    assert exc_info.value.code is ReplayErrorCode.ALREADY_REPLAYED


async def test_concurrent_replay_creates_one_child_and_failed_child_can_chain(
    replay_runtime,
):
    runtime, owner_id, session_id = replay_runtime
    source = await _failed_interview_job(runtime, owner_id, session_id, "concurrent")

    async def replay_once():
        async with runtime.session_scope() as session:
            return await JobReplayService(session, app_env="test").replay(
                job_id=source.id,
                operator="operator@example.com",
                reason="approved concurrent recovery",
                approved_by=None,
                dry_run=False,
            )

    results = await asyncio.gather(replay_once(), replay_once(), return_exceptions=True)
    outcomes = [result for result in results if not isinstance(result, BaseException)]
    errors = [result for result in results if isinstance(result, ReplayError)]
    assert len(outcomes) == 1
    assert [error.code for error in errors] == [ReplayErrorCode.ALREADY_REPLAYED]

    first_replay = outcomes[0].replay_job
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        await repository.claim(first_replay.id, lease_duration=timedelta(minutes=1))
        await repository.fail(first_replay.id, JobErrorCode.PROVIDER_ERROR)
    async with runtime.session_scope() as session:
        chained = await JobReplayService(session, app_env="test").replay(
            job_id=first_replay.id,
            operator="operator@example.com",
            reason="approved chained recovery",
            approved_by=None,
            dry_run=False,
        )

    assert chained.replay_job.replay_of == first_replay.id


async def test_nonfailed_and_incompatible_jobs_are_not_replayed(replay_runtime):
    runtime, owner_id, session_id = replay_runtime
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        pending = await repository.create(
            owner_id=owner_id,
            job_type="interview.finish",
            payload_ref={"schema_version": 1, "session_id": str(session_id)},
            idempotency_key="pending-source",
        )
        incompatible = await repository.create(
            owner_id=owner_id,
            job_type="legacy.unknown",
            payload_ref={"schema_version": 1, "session_id": str(session_id)},
            idempotency_key="legacy-source",
        )
        await repository.claim(
            incompatible.id, lease_duration=timedelta(minutes=1)
        )
        await repository.fail(incompatible.id, JobErrorCode.INTERNAL_ERROR)

    for job_id, error_code in (
        (pending.id, ReplayErrorCode.JOB_NOT_FAILED),
        (incompatible.id, ReplayErrorCode.PAYLOAD_INCOMPATIBLE),
    ):
        async with runtime.session_scope() as session:
            with pytest.raises(ReplayError) as exc_info:
                await JobReplayService(session, app_env="test").replay(
                    job_id=job_id,
                    operator="operator@example.com",
                    reason="approved compatibility recovery",
                    approved_by=None,
                    dry_run=True,
                )
        assert exc_info.value.code is error_code


async def test_interview_replay_rejects_session_outside_source_owner(
    replay_runtime,
):
    runtime, owner_id, _ = replay_runtime
    source = await _failed_interview_job(
        runtime,
        owner_id,
        uuid.uuid4(),
        "foreign-session",
    )

    async with runtime.session_scope() as session:
        with pytest.raises(ReplayError) as exc_info:
            await JobReplayService(session, app_env="test").replay(
                job_id=source.id,
                operator="operator@example.com",
                reason="reject mismatched session owner",
                approved_by=None,
                dry_run=True,
            )

    assert exc_info.value.code is ReplayErrorCode.PAYLOAD_INCOMPATIBLE


async def test_existing_interview_result_blocks_replay(replay_runtime):
    runtime, owner_id, session_id = replay_runtime
    source = await _failed_interview_job(runtime, owner_id, session_id, "has-result")
    async with runtime.session_scope() as session:
        session.add(
            InterviewReport(
                id=uuid.uuid4(),
                user_id=owner_id,
                session_id=session_id,
                scores_json={},
                feedback_json={},
                suggestions_json=[],
                position="Backend engineer",
                source="session",
            )
        )
    async with runtime.session_scope() as session:
        with pytest.raises(ReplayError) as exc_info:
            await JobReplayService(session, app_env="test").replay(
                job_id=source.id,
                operator="operator@example.com",
                reason="must not duplicate report",
                approved_by=None,
                dry_run=True,
            )
    assert exc_info.value.code is ReplayErrorCode.BUSINESS_RESULT_EXISTS


async def test_recording_replay_dry_run_is_read_only_then_resets_failed_state(
    replay_runtime,
):
    runtime, owner_id, _ = replay_runtime
    recording_id = uuid.uuid4()
    tos_key = f"users/{owner_id}/recordings/{recording_id}.wav"
    async with runtime.session_scope() as session:
        session.add(
            Recording(
                id=recording_id,
                user_id=owner_id,
                filename="interview.wav",
                ext="wav",
                position="Backend engineer",
                tos_key=tos_key,
                status="failed",
                error="safe public error",
            )
        )
        repository = JobRepository(session)
        source = await repository.create(
            owner_id=owner_id,
            job_type="recording.process",
            payload_ref={
                "schema_version": 1,
                "recording_id": str(recording_id),
                "tos_key": tos_key,
            },
            idempotency_key="recording-replay-source",
        )
        await repository.claim(source.id, lease_duration=timedelta(minutes=1))
        source = await repository.fail(source.id, JobErrorCode.PROVIDER_ERROR)

    async with runtime.session_scope() as session:
        await JobReplayService(session, app_env="test").replay(
            job_id=source.id,
            operator="operator@example.com",
            reason="validate recording recovery",
            approved_by=None,
            dry_run=True,
        )
    async with runtime.session_scope() as session:
        after_dry_run = await session.get(Recording, recording_id)
        assert after_dry_run.status == "failed"
        assert after_dry_run.error == "safe public error"

    async with runtime.session_scope() as session:
        outcome = await JobReplayService(session, app_env="test").replay(
            job_id=source.id,
            operator="operator@example.com",
            reason="execute recording recovery",
            approved_by=None,
            dry_run=False,
        )
    async with runtime.session_scope() as session:
        replayed_recording = await session.get(Recording, recording_id)

    assert outcome.replay_job.status is JobStatus.PENDING
    assert replayed_recording.status == "processing"
    assert replayed_recording.error is None
    assert replayed_recording.finished_at is None


def test_cli_requires_explicit_production_confirmation():
    with pytest.raises(ReplayCliError, match="PRODUCTION_CONFIRMATION_REQUIRED"):
        validate_execution_guard(app_env="production", confirm_production=False)

    validate_execution_guard(app_env="production", confirm_production=True)
    validate_execution_guard(app_env="test", confirm_production=False)


def test_cli_output_exposes_only_safe_replay_result_fields():
    source_id = uuid.uuid4()
    replay_id = uuid.uuid4()
    output = replay_output(
        source_job_id=source_id,
        replay_job_id=replay_id,
        status=JobStatus.PENDING,
        dry_run=False,
    )

    assert output == {
        "source_job_id": str(source_id),
        "replay_job_id": str(replay_id),
        "status": "pending",
        "dry_run": False,
    }
