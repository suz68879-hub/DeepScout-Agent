import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete

from config import Config
from db.engine import build_database_runtime
from db.models import AppUser
from services.jobs.repository import JobRepository
from services.jobs.types import JobConflictError, JobErrorCode, JobStatus


@pytest.fixture
async def job_runtime(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for job repository tests")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    runtime = build_database_runtime(Config())
    await runtime.start()

    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add_all(
            [
                AppUser(
                    id=owner_id,
                    username=f"job_owner_{owner_id.hex}",
                    password_hash="test-only",
                ),
                AppUser(
                    id=other_owner_id,
                    username=f"job_owner_{other_owner_id.hex}",
                    password_hash="test-only",
                ),
            ]
        )

    try:
        yield runtime, owner_id, other_owner_id
    finally:
        async with runtime.session_scope() as session:
            await session.execute(
                delete(AppUser).where(AppUser.id.in_([owner_id, other_owner_id]))
            )
        await runtime.close()


async def _create_job(
    runtime, owner_id, *, idempotency_key="job-key", max_attempts=5
):
    async with runtime.session_scope() as session:
        return await JobRepository(session).create(
            owner_id=owner_id,
            job_type="interview.finish",
            payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )


async def test_create_is_concurrently_idempotent_and_get_is_owner_scoped(job_runtime):
    runtime, owner_id, other_owner_id = job_runtime

    first, second = await asyncio.gather(
        _create_job(runtime, owner_id, idempotency_key="same-key"),
        _create_job(runtime, owner_id, idempotency_key="same-key"),
    )

    assert first.id == second.id
    assert first.status is JobStatus.PENDING
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        assert (await repository.get(owner_id, first.id)).id == first.id
        assert await repository.get(other_owner_id, first.id) is None


async def test_concurrent_claim_has_exactly_one_winner(job_runtime):
    runtime, owner_id, _ = job_runtime
    job = await _create_job(runtime, owner_id, idempotency_key="claim-key")

    async def claim_once():
        async with runtime.session_scope() as session:
            return await JobRepository(session).claim(
                job.id,
                lease_duration=timedelta(minutes=1),
            )

    results = await asyncio.gather(claim_once(), claim_once(), return_exceptions=True)

    winners = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [result for result in results if isinstance(result, JobConflictError)]
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert winners[0].status is JobStatus.RUNNING
    assert winners[0].started_at is not None
    assert winners[0].lease_expires_at is not None


async def test_success_is_terminal_and_keeps_only_reference_result(job_runtime):
    runtime, owner_id, _ = job_runtime
    job = await _create_job(runtime, owner_id, idempotency_key="success-key")
    report_id = uuid.uuid4()

    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        await repository.claim(job.id, lease_duration=timedelta(minutes=1))
        succeeded = await repository.succeed(
            job.id,
            result_ref={"schema_version": 1, "report_id": str(report_id)},
        )
        with pytest.raises(JobConflictError):
            await repository.fail(job.id, JobErrorCode.INTERNAL_ERROR)
        with pytest.raises(JobConflictError):
            await repository.requeue(job.id)
        with pytest.raises(JobConflictError):
            await repository.cancel(owner_id, job.id)

    assert succeeded.status is JobStatus.SUCCEEDED
    assert succeeded.result_ref == {"schema_version": 1, "report_id": str(report_id)}
    assert succeeded.finished_at is not None
    assert succeeded.lease_expires_at is None


async def test_fail_accepts_only_public_error_code(job_runtime):
    runtime, owner_id, _ = job_runtime
    job = await _create_job(runtime, owner_id, idempotency_key="failure-key")

    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        await repository.claim(job.id, lease_duration=timedelta(minutes=1))
        with pytest.raises(ValueError):
            await repository.fail(job.id, "password=secret\ninternal traceback")
        failed = await repository.fail(job.id, JobErrorCode.PROVIDER_ERROR)

    assert failed.status is JobStatus.FAILED
    assert failed.error_code is JobErrorCode.PROVIDER_ERROR
    assert failed.finished_at is not None


async def test_requeue_increments_attempt_and_cancel_is_owner_scoped(job_runtime):
    runtime, owner_id, other_owner_id = job_runtime
    job = await _create_job(runtime, owner_id, idempotency_key="retry-key")

    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        await repository.claim(job.id, lease_duration=timedelta(minutes=1))
        pending = await repository.requeue(job.id)
        with pytest.raises(JobConflictError):
            await repository.cancel(other_owner_id, job.id)
        cancelled = await repository.cancel(owner_id, job.id)
        with pytest.raises(JobConflictError):
            await repository.claim(job.id, lease_duration=timedelta(minutes=1))

    assert pending.status is JobStatus.PENDING
    assert pending.attempt == 1
    assert pending.lease_expires_at is None
    assert cancelled.status is JobStatus.CANCELLED


async def test_concurrent_lease_scanners_requeue_once_and_fail_exhausted_job(job_runtime):
    runtime, owner_id, _ = job_runtime
    expired = await _create_job(runtime, owner_id, idempotency_key="expired-key")
    future = await _create_job(runtime, owner_id, idempotency_key="future-key")
    exhausted = await _create_job(
        runtime,
        owner_id,
        idempotency_key="exhausted-key",
        max_attempts=1,
    )
    started_at = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        await repository.claim(
            expired.id,
            lease_duration=timedelta(seconds=1),
            now=started_at,
        )
        await repository.claim(
            future.id,
            lease_duration=timedelta(hours=1),
            now=started_at,
        )
        await repository.claim(
            exhausted.id,
            lease_duration=timedelta(seconds=1),
            now=started_at,
        )

    async def scan_once():
        async with runtime.session_scope() as session:
            return await JobRepository(session).requeue_expired(
                now=started_at + timedelta(seconds=2),
                limit=10,
            )

    scan_results = await asyncio.gather(scan_once(), scan_once())
    transitioned = [job for result in scan_results for job in result]

    assert {job.id for job in transitioned} == {expired.id, exhausted.id}
    assert len(transitioned) == 2
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        recovered = await repository.get(owner_id, expired.id)
        still_running = await repository.get(owner_id, future.id)
        maxed_out = await repository.get(owner_id, exhausted.id)
    assert recovered.status is JobStatus.PENDING
    assert recovered.attempt == 1
    assert still_running.status is JobStatus.RUNNING
    assert maxed_out.status is JobStatus.FAILED
    assert maxed_out.error_code is JobErrorCode.MAX_ATTEMPTS_EXCEEDED


async def test_repository_does_not_commit_caller_transaction(job_runtime):
    runtime, owner_id, _ = job_runtime

    async with runtime.session_factory() as session:
        created = await JobRepository(session).create(
            owner_id=owner_id,
            job_type="interview.finish",
            payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
            idempotency_key="rollback-key",
        )
        await session.rollback()

    async with runtime.session_scope() as session:
        assert await JobRepository(session).get(owner_id, created.id) is None


async def test_create_hides_database_details_for_unknown_owner(job_runtime):
    runtime, _, _ = job_runtime

    async with runtime.session_scope() as session:
        with pytest.raises(JobConflictError, match="job creation conflict"):
            await JobRepository(session).create(
                owner_id=uuid.uuid4(),
                job_type="interview.finish",
                payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
                idempotency_key="unknown-owner-key",
            )


async def test_repository_rejects_invalid_timing_and_scan_bounds(job_runtime):
    runtime, owner_id, _ = job_runtime
    job = await _create_job(runtime, owner_id, idempotency_key="invalid-bounds-key")

    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        with pytest.raises(ValueError, match="lease_duration must be positive"):
            await repository.claim(job.id, lease_duration=timedelta(0))
        with pytest.raises(ValueError, match="timestamp must include a timezone"):
            await repository.claim(
                job.id,
                lease_duration=timedelta(seconds=1),
                now=datetime(2026, 8, 25, 8, 0),
            )
        with pytest.raises(ValueError, match="limit must be between 1 and 100"):
            await repository.requeue_expired(limit=0)
