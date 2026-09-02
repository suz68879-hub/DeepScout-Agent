"""Celery worker for durable recording-analysis jobs."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx

from config import Config, settings
from db import close_database, get_database_runtime, init_database
from db.engine import DatabaseRuntime
from db.models import Recording
from middleware.request_context import job_consumer_span
from services.asr_client import AsrError
from services.jobs.handlers import JobType
from services.jobs.repository import JobRepository
from services.jobs.types import (
    JobConflictError,
    JobErrorCode,
    JobRecord,
    JobStatus,
)
from services.recording_service import (
    RecordingModelOutputError,
    RecordingPollPending,
    RecordingStateError,
    asr_poll_timed_out,
    POLL_INTERVAL_SECONDS,
    process_recording as process_recording_step,
)
from services.storage import close_storage, init_storage
from tasks.celery_app import celery_app
from tasks.retry_policy import (
    DeadLetterPublisher,
    FailureClassification,
    classify_exception,
    countdown_for_retry,
    decide_retry,
)

logger = logging.getLogger(__name__)
RecordingProcessor = Callable[[JobRecord], Awaitable[dict]]


class RecordingTaskMessageError(Exception):
    """A broker message does not reference a valid recording job."""


class RecordingTaskPending(Exception):
    """ASR is pending and Celery should redeliver after a countdown."""

    def __init__(self, job_id: str, countdown: int):
        super().__init__(job_id)
        self.countdown = countdown


def _summary(job: JobRecord) -> dict:
    result = {"job_id": str(job.id), "status": job.status.value}
    if job.result_ref is not None:
        result["result_ref"] = job.result_ref
    if job.error_code is not None:
        result["error_code"] = job.error_code.value
    return result


def _validate_message(schema_version: int, job_id: str, job_type: str) -> uuid.UUID:
    if (
        type(schema_version) is not int
        or schema_version != 1
        or not isinstance(job_id, str)
        or job_type != JobType.RECORDING_PROCESS.value
    ):
        raise RecordingTaskMessageError("INVALID_RECORDING_TASK_MESSAGE")
    try:
        return uuid.UUID(job_id)
    except (TypeError, ValueError, AttributeError):
        raise RecordingTaskMessageError("INVALID_RECORDING_TASK_MESSAGE") from None


def _validate_result(result: dict, recording_id: str) -> dict:
    if (
        not isinstance(result, dict)
        or frozenset(result) != {"schema_version", "recording_id", "report_id"}
        or type(result.get("schema_version")) is not int
        or result.get("schema_version") != 1
        or result.get("recording_id") != recording_id
    ):
        raise RecordingStateError("INVALID_RECORDING_RESULT")
    try:
        uuid.UUID(result["report_id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RecordingStateError("INVALID_RECORDING_RESULT") from None
    return dict(result)


async def _mark_failed_recording(session, job: JobRecord, error: str) -> None:
    try:
        recording_id = uuid.UUID(job.payload_ref["recording_id"])
        tos_key = job.payload_ref["tos_key"]
    except (KeyError, TypeError, ValueError, AttributeError):
        return
    recording = await session.get(Recording, recording_id)
    if (
        recording is not None
        and recording.user_id == job.owner_id
        and recording.tos_key == tos_key
        and recording.status == "processing"
    ):
        recording.status = "failed"
        recording.error = error
        recording.finished_at = datetime.now(timezone.utc)


async def _resolve_failure(
    runtime: DatabaseRuntime,
    job: JobRecord,
    failure: FailureClassification,
    recording_error: str,
) -> dict:
    decision = decide_retry(
        failure,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
    )
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        resolved = await repository.resolve_failure(
            job.id,
            decision.error_code,
            retryable=decision.should_retry,
        )
        if resolved.status is JobStatus.FAILED:
            await _mark_failed_recording(session, job, recording_error)
    if resolved.status is JobStatus.PENDING:
        raise RecordingTaskPending(str(job.id), decision.countdown)
    logger.warning(
        "Recording job failed",
        extra={
            "event": "recording_job_failed",
            "job_id": str(job.id),
            "error_code": resolved.error_code.value,
        },
    )
    return _summary(resolved)


async def execute_recording_job(
    runtime: DatabaseRuntime,
    *,
    schema_version: int,
    job_id: str,
    job_type: str,
    processor: RecordingProcessor = process_recording_step,
) -> dict:
    locked_job_id = _validate_message(schema_version, job_id, job_type)
    retry_countdown = None
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        job = await repository.get_internal(locked_job_id)
        if job is None or job.job_type != JobType.RECORDING_PROCESS.value:
            raise RecordingTaskMessageError("RECORDING_JOB_NOT_FOUND")
        if job.status is JobStatus.RUNNING:
            recovered = await repository.recover_expired(job.id)
            if recovered is None:
                retry_countdown = 5
            elif recovered.status is JobStatus.PENDING:
                retry_countdown = countdown_for_retry(recovered.attempt)
            elif recovered.status is JobStatus.FAILED:
                await _mark_failed_recording(
                    session,
                    job,
                    "recording worker retry limit exceeded",
                )
            if recovered is not None:
                job = recovered
        if retry_countdown is None and job.status is not JobStatus.PENDING:
            return _summary(job)
        if retry_countdown is None:
            claimed = await repository.claim(
                job.id, lease_duration=timedelta(minutes=5)
            )
    if retry_countdown is not None:
        raise RecordingTaskPending(str(job.id), retry_countdown)

    try:
        recording_id = str(uuid.UUID(claimed.payload_ref["recording_id"]))
        result_ref = _validate_result(await processor(claimed), recording_id)
    except RecordingPollPending as exc:
        if asr_poll_timed_out(claimed.started_at):
            return await _resolve_failure(
                runtime,
                claimed,
                FailureClassification(JobErrorCode.PROVIDER_ERROR, False),
                "transcription timed out",
            )
        try:
            async with runtime.session_scope() as session:
                await JobRepository(session).release(claimed.id)
        except JobConflictError:
            pass
        raise RecordingTaskPending(str(claimed.id), POLL_INTERVAL_SECONDS) from exc
    except (httpx.RequestError, OSError) as exc:
        failure = (
            FailureClassification(JobErrorCode.NETWORK_ERROR, True)
            if isinstance(exc, httpx.RequestError)
            else classify_exception(exc)
        )
        try:
            return await _resolve_failure(
                runtime, claimed, failure, "recording analysis failed"
            )
        except RecordingTaskPending as pending:
            raise pending from exc
    except AsrError as exc:
        retryable = exc.status_code.startswith("5") or "429" in exc.status_code
        error_code = (
            JobErrorCode.RATE_LIMITED
            if "429" in exc.status_code
            else JobErrorCode.PROVIDER_ERROR
        )
        try:
            return await _resolve_failure(
                runtime,
                claimed,
                FailureClassification(error_code, retryable),
                "speech recognition failed",
            )
        except RecordingTaskPending as pending:
            raise pending from exc
    except RecordingModelOutputError:
        return await _resolve_failure(
            runtime,
            claimed,
            FailureClassification(JobErrorCode.PROVIDER_ERROR, False),
            "recording analysis failed",
        )
    except (RecordingStateError, KeyError, TypeError, ValueError):
        return await _resolve_failure(
            runtime,
            claimed,
            FailureClassification(JobErrorCode.INVALID_INPUT, False),
            "recording input is invalid",
        )
    except Exception as exc:
        logger.error(
            "Recording job raised an internal error",
            extra={
                "event": "recording_job_internal_error",
                "job_id": str(claimed.id),
                "error_code": JobErrorCode.INTERNAL_ERROR.value,
                "error_type": type(exc).__name__,
            },
        )
        return await _resolve_failure(
            runtime,
            claimed,
            FailureClassification(JobErrorCode.INTERNAL_ERROR, False),
            "recording analysis failed",
        )

    async with runtime.session_scope() as session:
        succeeded = await JobRepository(session).succeed(
            claimed.id, result_ref=result_ref
        )
    logger.info(
        "Recording job succeeded",
        extra={"event": "recording_job_succeeded", "job_id": str(claimed.id)},
    )
    return _summary(succeeded)


async def run_recording_job(
    config: Config,
    *,
    schema_version: int,
    job_id: str,
    job_type: str,
) -> dict:
    await init_database(config)
    try:
        await init_storage()
        return await execute_recording_job(
            get_database_runtime(),
            schema_version=schema_version,
            job_id=job_id,
            job_type=job_type,
        )
    finally:
        try:
            await close_storage()
        finally:
            await close_database()


def _process_recording(self, schema_version: int, job_id: str, job_type: str) -> dict:
    try:
        result = asyncio.run(
            run_recording_job(
                settings,
                schema_version=schema_version,
                job_id=job_id,
                job_type=job_type,
            )
        )
    except RecordingTaskPending as exc:
        raise self.retry(exc=exc, countdown=exc.countdown, max_retries=None)
    if result.get("status") == JobStatus.FAILED.value:
        DeadLetterPublisher(celery_app).publish(
            job_id=result["job_id"],
            error_code=result["error_code"],
            original_queue="deepscout.recording",
        )
    return result


@celery_app.task(
    bind=True,
    name="tasks.recording_tasks.process_recording",
    ignore_result=True,
)
def process_recording(self, schema_version: int, job_id: str, job_type: str) -> dict:
    headers = getattr(self.request, "headers", None)
    with job_consumer_span(
        headers=headers,
        job_id=job_id,
        operation=JobType.RECORDING_PROCESS.value,
    ):
        return _process_recording(self, schema_version, job_id, job_type)
