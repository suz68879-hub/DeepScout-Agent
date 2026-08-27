import json
import uuid
from datetime import timedelta
from enum import StrEnum

from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import OutboxEvent
from services.jobs.handlers import (
    JobHandlerError,
    JobHandlerRegistry,
    JobType,
)
from services.jobs.repository import JobRepository
from services.jobs.types import JobConflictError, JobErrorCode, JobRecord, JobStatus


class DispatchErrorCode(StrEnum):
    UNKNOWN_JOB_TYPE = "UNKNOWN_JOB_TYPE"
    INVALID_OWNER = "INVALID_OWNER"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    INVALID_IDEMPOTENCY_KEY = "INVALID_IDEMPOTENCY_KEY"
    JOB_CONFLICT = "JOB_CONFLICT"
    INLINE_FORBIDDEN = "INLINE_FORBIDDEN"
    HANDLER_NOT_CONFIGURED = "HANDLER_NOT_CONFIGURED"


class JobDispatchError(ValueError):
    def __init__(self, code: DispatchErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


_PAYLOAD_RULES = {
    JobType.INTERVIEW_FINISH: {
        "required": frozenset({"schema_version", "session_id"}),
        "allowed": frozenset({"schema_version", "session_id", "step"}),
        "uuid_fields": frozenset({"session_id"}),
    },
    JobType.RECORDING_PROCESS: {
        "required": frozenset({"schema_version", "recording_id", "tos_key"}),
        "allowed": frozenset({"schema_version", "recording_id", "tos_key"}),
        "uuid_fields": frozenset({"recording_id"}),
    },
}


def _trace_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return {
        key: value
        for key, value in carrier.items()
        if key in {"traceparent", "tracestate"}
    }


def _job_type(value: JobType | str) -> JobType:
    try:
        return JobType(value)
    except (TypeError, ValueError):
        raise JobDispatchError(DispatchErrorCode.UNKNOWN_JOB_TYPE) from None


def _owner_id(value: uuid.UUID | str | None) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value or "")
    except (TypeError, ValueError, AttributeError):
        raise JobDispatchError(DispatchErrorCode.INVALID_OWNER) from None


def _idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise JobDispatchError(DispatchErrorCode.INVALID_IDEMPOTENCY_KEY)
    return value


def validate_job_payload(job_type: JobType, value: dict) -> dict:
    if not isinstance(value, dict):
        raise JobDispatchError(DispatchErrorCode.INVALID_PAYLOAD)
    rules = _PAYLOAD_RULES[job_type]
    keys = frozenset(value)
    if not rules["required"] <= keys or not keys <= rules["allowed"]:
        raise JobDispatchError(DispatchErrorCode.INVALID_PAYLOAD)
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise JobDispatchError(DispatchErrorCode.INVALID_PAYLOAD)
    try:
        for field in rules["uuid_fields"]:
            uuid.UUID(value[field])
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, AttributeError):
        raise JobDispatchError(DispatchErrorCode.INVALID_PAYLOAD) from None
    if len(encoded) > 4096:
        raise JobDispatchError(DispatchErrorCode.INVALID_PAYLOAD)
    for field in ("tos_key", "step"):
        if field in value and (not isinstance(value[field], str) or not value[field]):
            raise JobDispatchError(DispatchErrorCode.INVALID_PAYLOAD)
    return dict(value)


class JobDispatcher:
    """Create a Job and its Outbox event without committing the caller transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._jobs = JobRepository(session)

    async def enqueue(
        self,
        *,
        job_type: JobType | str,
        owner_id: uuid.UUID | str | None,
        payload_ref: dict,
        idempotency_key: str,
    ) -> JobRecord:
        locked_type = _job_type(job_type)
        locked_owner = _owner_id(owner_id)
        locked_payload = validate_job_payload(locked_type, payload_ref)
        locked_key = _idempotency_key(idempotency_key)
        try:
            job = await self._jobs.create(
                owner_id=locked_owner,
                job_type=locked_type.value,
                payload_ref=locked_payload,
                idempotency_key=locked_key,
            )
        except JobConflictError:
            raise JobDispatchError(DispatchErrorCode.JOB_CONFLICT) from None

        event_payload = {
            "schema_version": 1,
            "job_id": str(job.id),
            "job_type": job.job_type,
        }
        trace_context = _trace_context()
        if trace_context:
            event_payload["trace_context"] = trace_context
        await self._session.execute(
            insert(OutboxEvent)
            .values(
                id=uuid.uuid4(),
                aggregate_type="background_job",
                aggregate_id=job.id,
                event_type="job.created",
                payload=event_payload,
            )
            .on_conflict_do_nothing(constraint="uq_outbox_event_aggregate_event")
        )
        return job


class InlineJobDispatcher(JobDispatcher):
    """Explicit non-production adapter that executes handlers via the state machine."""

    def __init__(
        self,
        session: AsyncSession,
        handlers: JobHandlerRegistry,
        *,
        app_env: str,
    ) -> None:
        if settings.APP_ENV == "production" or app_env not in {"development", "test"}:
            raise JobDispatchError(DispatchErrorCode.INLINE_FORBIDDEN)
        super().__init__(session)
        self._handlers = handlers

    async def enqueue(
        self,
        *,
        job_type: JobType | str,
        owner_id: uuid.UUID | str | None,
        payload_ref: dict,
        idempotency_key: str,
    ) -> JobRecord:
        locked_type = _job_type(job_type)
        try:
            handler = self._handlers.resolve(locked_type)
        except KeyError:
            raise JobDispatchError(DispatchErrorCode.HANDLER_NOT_CONFIGURED) from None

        job = await super().enqueue(
            job_type=locked_type,
            owner_id=owner_id,
            payload_ref=payload_ref,
            idempotency_key=idempotency_key,
        )
        if job.status is not JobStatus.PENDING:
            return job
        try:
            claimed = await self._jobs.claim(
                job.id,
                lease_duration=timedelta(minutes=5),
            )
        except JobConflictError:
            raise JobDispatchError(DispatchErrorCode.JOB_CONFLICT) from None

        try:
            result_ref = await handler(claimed)
        except JobHandlerError as exc:
            return await self._jobs.fail(job.id, exc.error_code)
        except Exception:
            return await self._jobs.fail(job.id, JobErrorCode.INTERNAL_ERROR)
        return await self._jobs.succeed(job.id, result_ref=result_ref)
