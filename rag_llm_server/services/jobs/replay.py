import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import BackgroundJob, InterviewReport, InterviewSession, Recording
from services.jobs.dispatcher import (
    JobDispatchError,
    JobDispatcher,
    validate_job_payload,
)
from services.jobs.handlers import JobType
from services.jobs.repository import JobRepository
from services.jobs.types import JobRecord, JobStatus


class ReplayErrorCode(StrEnum):
    INVALID_OPERATOR = "INVALID_OPERATOR"
    INVALID_REASON = "INVALID_REASON"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    INVALID_JOB_ID = "INVALID_JOB_ID"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_NOT_FAILED = "JOB_NOT_FAILED"
    ALREADY_REPLAYED = "ALREADY_REPLAYED"
    PAYLOAD_INCOMPATIBLE = "PAYLOAD_INCOMPATIBLE"
    BUSINESS_RESULT_EXISTS = "BUSINESS_RESULT_EXISTS"
    BUSINESS_STATE_CONFLICT = "BUSINESS_STATE_CONFLICT"


class ReplayError(ValueError):
    def __init__(self, code: ReplayErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class ReplayAuthorization:
    operator: str
    reason: str
    approved_by: str | None


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    source_job_id: uuid.UUID
    replay_job: JobRecord | None
    dry_run: bool


def _identity(value: str | None) -> str:
    identity = value.strip() if isinstance(value, str) else ""
    if (
        not 3 <= len(identity) <= 128
        or any(character.isspace() for character in identity)
        or any(unicodedata.category(character).startswith("C") for character in identity)
    ):
        raise ReplayError(ReplayErrorCode.INVALID_OPERATOR)
    return identity


def validate_replay_authorization(
    *,
    operator: str,
    reason: str,
    approved_by: str | None,
    app_env: str,
) -> ReplayAuthorization:
    locked_operator = _identity(operator)
    locked_reason = reason.strip() if isinstance(reason, str) else ""
    if not 10 <= len(locked_reason) <= 512 or any(
        unicodedata.category(character).startswith("C")
        for character in locked_reason
    ):
        raise ReplayError(ReplayErrorCode.INVALID_REASON)
    locked_approver = _identity(approved_by) if approved_by is not None else None
    if app_env == "production" and (
        locked_approver is None
        or locked_approver.casefold() == locked_operator.casefold()
    ):
        raise ReplayError(ReplayErrorCode.APPROVAL_REQUIRED)
    return ReplayAuthorization(
        operator=locked_operator,
        reason=locked_reason,
        approved_by=locked_approver,
    )


class JobReplayService:
    def __init__(self, session: AsyncSession, *, app_env: str) -> None:
        self._session = session
        self._app_env = app_env

    async def replay(
        self,
        *,
        job_id: uuid.UUID | str,
        operator: str,
        reason: str,
        approved_by: str | None,
        dry_run: bool,
    ) -> ReplayOutcome:
        authorization = validate_replay_authorization(
            operator=operator,
            reason=reason,
            approved_by=approved_by,
            app_env=self._app_env,
        )
        try:
            source_id = job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(job_id)
        except (TypeError, ValueError, AttributeError):
            raise ReplayError(ReplayErrorCode.INVALID_JOB_ID) from None
        source = await self._session.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == source_id)
            .with_for_update()
        )
        if source is None:
            raise ReplayError(ReplayErrorCode.JOB_NOT_FOUND)
        if source.status != JobStatus.FAILED.value:
            raise ReplayError(ReplayErrorCode.JOB_NOT_FAILED)
        if await self._session.scalar(
            select(BackgroundJob.id).where(BackgroundJob.replay_of == source.id)
        ):
            raise ReplayError(ReplayErrorCode.ALREADY_REPLAYED)
        try:
            job_type = JobType(source.job_type)
            payload_ref = validate_job_payload(job_type, source.payload_ref)
        except (ValueError, JobDispatchError):
            raise ReplayError(ReplayErrorCode.PAYLOAD_INCOMPATIBLE) from None
        if job_type is JobType.INTERVIEW_FINISH:
            session_id = uuid.UUID(payload_ref["session_id"])
            if not await self._session.scalar(
                select(InterviewSession.id).where(
                    InterviewSession.id == session_id,
                    InterviewSession.user_id == source.owner_id,
                )
            ):
                raise ReplayError(ReplayErrorCode.PAYLOAD_INCOMPATIBLE)
            if await self._session.scalar(
                select(InterviewReport.id).where(
                    InterviewReport.session_id == session_id,
                    InterviewReport.user_id == source.owner_id,
                )
            ):
                raise ReplayError(ReplayErrorCode.BUSINESS_RESULT_EXISTS)
        recording = None
        if job_type is JobType.RECORDING_PROCESS:
            recording = await self._session.scalar(
                select(Recording).where(
                    Recording.id == uuid.UUID(payload_ref["recording_id"]),
                    Recording.user_id == source.owner_id,
                )
            )
            if recording is None or recording.tos_key != payload_ref["tos_key"]:
                raise ReplayError(ReplayErrorCode.PAYLOAD_INCOMPATIBLE)
            if recording.status == "done" or recording.report_id is not None:
                raise ReplayError(ReplayErrorCode.BUSINESS_RESULT_EXISTS)
            if recording.status != "failed":
                raise ReplayError(ReplayErrorCode.BUSINESS_STATE_CONFLICT)
        if dry_run:
            return ReplayOutcome(source.id, None, True)

        if recording is not None:
            recording.status = "processing"
            recording.error = None
            recording.finished_at = None

        replay_job = await JobDispatcher(self._session).enqueue(
            job_type=job_type,
            owner_id=source.owner_id,
            payload_ref=payload_ref,
            idempotency_key=f"replay:{source.id}",
        )
        replay_model = await self._session.get(BackgroundJob, replay_job.id)
        replay_model.replay_of = source.id
        replay_model.replay_operator = authorization.operator
        replay_model.replay_approved_by = authorization.approved_by
        replay_model.replay_reason = authorization.reason
        replay_model.replayed_at = datetime.now(timezone.utc)
        await self._session.flush()
        persisted = await JobRepository(self._session).get_internal(replay_job.id)
        return ReplayOutcome(source.id, persisted, False)
