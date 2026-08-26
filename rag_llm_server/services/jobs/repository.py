import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import BackgroundJob
from services.jobs.types import JobConflictError, JobErrorCode, JobRecord, JobStatus


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return current


def _record(model: BackgroundJob) -> JobRecord:
    return JobRecord(
        id=model.id,
        owner_id=model.owner_id,
        job_type=model.job_type,
        status=JobStatus(model.status),
        idempotency_key=model.idempotency_key,
        payload_ref=dict(model.payload_ref),
        result_ref=dict(model.result_ref) if model.result_ref is not None else None,
        attempt=model.attempt,
        max_attempts=model.max_attempts,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        lease_expires_at=model.lease_expires_at,
        error_code=JobErrorCode(model.error_code) if model.error_code else None,
    )


class JobRepository:
    """Job persistence bound to one caller-owned AsyncSession transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID | str,
        job_type: str,
        payload_ref: dict,
        idempotency_key: str,
        max_attempts: int = 5,
    ) -> JobRecord:
        owner_uuid = _as_uuid(owner_id)
        model = BackgroundJob(
            id=uuid.uuid4(),
            owner_id=owner_uuid,
            job_type=job_type,
            payload_ref=dict(payload_ref),
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(model)
                await self._session.flush()
        except IntegrityError:
            existing = await self._session.scalar(
                select(BackgroundJob).where(
                    BackgroundJob.owner_id == owner_uuid,
                    BackgroundJob.job_type == job_type,
                    BackgroundJob.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise JobConflictError("job creation conflict") from None
            return _record(existing)
        return _record(model)

    async def get(
        self, owner_id: uuid.UUID | str, job_id: uuid.UUID | str
    ) -> JobRecord | None:
        model = await self._session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == _as_uuid(job_id),
                BackgroundJob.owner_id == _as_uuid(owner_id),
            )
        )
        return _record(model) if model else None

    async def get_internal(self, job_id: uuid.UUID | str) -> JobRecord | None:
        model = await self._session.get(BackgroundJob, _as_uuid(job_id))
        return _record(model) if model else None

    async def latest_for_session(
        self, session_id: uuid.UUID | str
    ) -> JobRecord | None:
        model = await self._session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == "interview.finish",
                BackgroundJob.payload_ref["session_id"].astext
                == str(_as_uuid(session_id)),
            )
            .order_by(BackgroundJob.created_at.desc(), BackgroundJob.id.desc())
            .limit(1)
        )
        return _record(model) if model else None

    async def has_unfinished_predecessor(self, job: JobRecord) -> bool:
        session_id = job.payload_ref.get("session_id")
        if not isinstance(session_id, str):
            raise ValueError("interview job is missing session_id")
        predecessor = await self._session.scalar(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.id != job.id,
                BackgroundJob.owner_id == job.owner_id,
                BackgroundJob.job_type == job.job_type,
                BackgroundJob.payload_ref["session_id"].astext == session_id,
                BackgroundJob.status.in_(
                    [JobStatus.PENDING.value, JobStatus.RUNNING.value]
                ),
                or_(
                    BackgroundJob.created_at < job.created_at,
                    and_(
                        BackgroundJob.created_at == job.created_at,
                        BackgroundJob.id < job.id,
                    ),
                ),
            )
            .limit(1)
        )
        return predecessor is not None

    async def claim(
        self,
        job_id: uuid.UUID | str,
        *,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> JobRecord:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        claimed_at = _now(now)
        model = await self._session.scalar(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == _as_uuid(job_id),
                BackgroundJob.status == JobStatus.PENDING.value,
                BackgroundJob.attempt <= BackgroundJob.max_attempts,
            )
            .values(
                status=JobStatus.RUNNING.value,
                started_at=func.coalesce(BackgroundJob.started_at, claimed_at),
                updated_at=claimed_at,
                lease_expires_at=claimed_at + lease_duration,
            )
            .returning(BackgroundJob)
        )
        if model is None:
            raise JobConflictError("job state conflict")
        return _record(model)

    async def succeed(
        self,
        job_id: uuid.UUID | str,
        *,
        result_ref: dict,
        now: datetime | None = None,
    ) -> JobRecord:
        finished_at = _now(now)
        return await self._finish(
            job_id,
            status=JobStatus.SUCCEEDED,
            finished_at=finished_at,
            result_ref=dict(result_ref),
            error_code=None,
        )

    async def fail(
        self,
        job_id: uuid.UUID | str,
        error_code: JobErrorCode | str,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        public_code = JobErrorCode(error_code)
        finished_at = _now(now)
        return await self._finish(
            job_id,
            status=JobStatus.FAILED,
            finished_at=finished_at,
            error_code=public_code.value,
        )

    async def _finish(
        self,
        job_id: uuid.UUID | str,
        *,
        status: JobStatus,
        finished_at: datetime,
        **values,
    ) -> JobRecord:
        model = await self._session.scalar(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == _as_uuid(job_id),
                BackgroundJob.status == JobStatus.RUNNING.value,
            )
            .values(
                status=status.value,
                updated_at=finished_at,
                finished_at=finished_at,
                lease_expires_at=None,
                **values,
            )
            .returning(BackgroundJob)
        )
        if model is None:
            raise JobConflictError("job state conflict")
        return _record(model)

    async def requeue(
        self,
        job_id: uuid.UUID | str,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        requeued_at = _now(now)
        model = await self._session.scalar(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == _as_uuid(job_id),
                BackgroundJob.status == JobStatus.RUNNING.value,
                BackgroundJob.attempt < BackgroundJob.max_attempts,
            )
            .values(
                status=JobStatus.PENDING.value,
                attempt=BackgroundJob.attempt + 1,
                updated_at=requeued_at,
                lease_expires_at=None,
                error_code=None,
            )
            .returning(BackgroundJob)
        )
        if model is None:
            raise JobConflictError("job state conflict")
        return _record(model)

    async def resolve_failure(
        self,
        job_id: uuid.UUID | str,
        error_code: JobErrorCode | str,
        *,
        retryable: bool,
        now: datetime | None = None,
    ) -> JobRecord:
        """Atomically requeue a retryable failure or persist one terminal state."""
        public_code = JobErrorCode(error_code)
        resolved_at = _now(now)
        can_retry = and_(
            retryable,
            BackgroundJob.attempt < BackgroundJob.max_attempts,
        )
        model = await self._session.scalar(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == _as_uuid(job_id),
                BackgroundJob.status == JobStatus.RUNNING.value,
            )
            .values(
                status=case(
                    (can_retry, JobStatus.PENDING.value),
                    else_=JobStatus.FAILED.value,
                ),
                attempt=case(
                    (can_retry, BackgroundJob.attempt + 1),
                    else_=BackgroundJob.attempt,
                ),
                updated_at=resolved_at,
                finished_at=case((can_retry, None), else_=resolved_at),
                lease_expires_at=None,
                error_code=case(
                    (can_retry, None),
                    (
                        retryable,
                        JobErrorCode.MAX_ATTEMPTS_EXCEEDED.value,
                    ),
                    else_=public_code.value,
                ),
            )
            .returning(BackgroundJob)
        )
        if model is None:
            raise JobConflictError("job state conflict")
        return _record(model)

    async def cancel(
        self,
        owner_id: uuid.UUID | str,
        job_id: uuid.UUID | str,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        cancelled_at = _now(now)
        model = await self._session.scalar(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == _as_uuid(job_id),
                BackgroundJob.owner_id == _as_uuid(owner_id),
                BackgroundJob.status.in_(
                    [JobStatus.PENDING.value, JobStatus.RUNNING.value]
                ),
            )
            .values(
                status=JobStatus.CANCELLED.value,
                updated_at=cancelled_at,
                finished_at=cancelled_at,
                lease_expires_at=None,
            )
            .returning(BackgroundJob)
        )
        if model is None:
            raise JobConflictError("job state conflict")
        return _record(model)

    async def requeue_expired(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        scanned_at = _now(now)
        candidate_ids = (
            select(BackgroundJob.id)
            .where(
                BackgroundJob.status == JobStatus.RUNNING.value,
                BackgroundJob.lease_expires_at.is_not(None),
                BackgroundJob.lease_expires_at <= scanned_at,
            )
            .order_by(BackgroundJob.lease_expires_at, BackgroundJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        can_retry = BackgroundJob.attempt < BackgroundJob.max_attempts
        models = (
            await self._session.scalars(
                update(BackgroundJob)
                .where(BackgroundJob.id.in_(candidate_ids))
                .values(
                    status=case(
                        (can_retry, JobStatus.PENDING.value),
                        else_=JobStatus.FAILED.value,
                    ),
                    attempt=case(
                        (can_retry, BackgroundJob.attempt + 1),
                        else_=BackgroundJob.attempt,
                    ),
                    updated_at=scanned_at,
                    finished_at=case((can_retry, None), else_=scanned_at),
                    lease_expires_at=None,
                    error_code=case(
                        (can_retry, None),
                        else_=JobErrorCode.MAX_ATTEMPTS_EXCEEDED.value,
                    ),
                )
                .returning(BackgroundJob)
            )
        ).all()
        return [_record(model) for model in models]

    async def recover_expired(
        self,
        job_id: uuid.UUID | str,
        *,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Recover one redelivered worker-lost job using its persisted lease."""
        recovered_at = _now(now)
        can_retry = BackgroundJob.attempt < BackgroundJob.max_attempts
        model = await self._session.scalar(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == _as_uuid(job_id),
                BackgroundJob.status == JobStatus.RUNNING.value,
                BackgroundJob.lease_expires_at.is_not(None),
                BackgroundJob.lease_expires_at <= recovered_at,
            )
            .values(
                status=case(
                    (can_retry, JobStatus.PENDING.value),
                    else_=JobStatus.FAILED.value,
                ),
                attempt=case(
                    (can_retry, BackgroundJob.attempt + 1),
                    else_=BackgroundJob.attempt,
                ),
                updated_at=recovered_at,
                finished_at=case((can_retry, None), else_=recovered_at),
                lease_expires_at=None,
                error_code=case(
                    (can_retry, None),
                    else_=JobErrorCode.MAX_ATTEMPTS_EXCEEDED.value,
                ),
            )
            .returning(BackgroundJob)
        )
        return _record(model) if model is not None else None
