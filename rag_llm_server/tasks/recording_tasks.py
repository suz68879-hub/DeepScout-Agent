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
    POLL_INTERVAL_SECONDS,
    RecordingModelOutputError,
    RecordingPollPending,
    RecordingStateError,
    process_recording as process_recording_step,
)
from services.storage import close_storage, init_storage
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
RecordingProcessor = Callable[[JobRecord], Awaitable[dict]]


class RecordingTaskMessageError(Exception):
    """A broker message does not reference a valid recording job."""


class RecordingTaskPending(Exception):
    """ASR is pending and Celery should redeliver after a countdown."""


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


async def _requeue_or_timeout(
    runtime: DatabaseRuntime, job: JobRecord
) -> dict | None:
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        try:
            await repository.requeue(job.id)
        except JobConflictError:
            await _mark_failed_recording(session, job, "transcription timed out")
            failed = await repository.fail(
                job.id, JobErrorCode.MAX_ATTEMPTS_EXCEEDED
            )
            return _summary(failed)
    return None


async def _fail_job(
    runtime: DatabaseRuntime,
    job: JobRecord,
    error_code: JobErrorCode,
    recording_error: str,
) -> dict:
    async with runtime.session_scope() as session:
        await _mark_failed_recording(session, job, recording_error)
        failed = await JobRepository(session).fail(job.id, error_code)
    logger.warning(
        "Recording job failed",
        extra={
            "event": "recording_job_failed",
            "job_id": str(job.id),
            "error_code": error_code.value,
        },
    )
    return _summary(failed)


async def execute_recording_job(
    runtime: DatabaseRuntime,
    *,
    schema_version: int,
    job_id: str,
    job_type: str,
    processor: RecordingProcessor = process_recording_step,
) -> dict:
    locked_job_id = _validate_message(schema_version, job_id, job_type)
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        job = await repository.get_internal(locked_job_id)
        if job is None or job.job_type != JobType.RECORDING_PROCESS.value:
            raise RecordingTaskMessageError("RECORDING_JOB_NOT_FOUND")
        if job.status is not JobStatus.PENDING:
            return _summary(job)
        claimed = await repository.claim(
            job.id, lease_duration=timedelta(minutes=5)
        )

    try:
        recording_id = str(uuid.UUID(claimed.payload_ref["recording_id"]))
        result_ref = _validate_result(await processor(claimed), recording_id)
    except RecordingPollPending as exc:
        terminal = await _requeue_or_timeout(runtime, claimed)
        if terminal is not None:
            return terminal
        raise RecordingTaskPending(str(claimed.id)) from exc
    except (httpx.RequestError, OSError) as exc:
        terminal = await _requeue_or_timeout(runtime, claimed)
        if terminal is not None:
            return terminal
        raise RecordingTaskPending(str(claimed.id)) from exc
    except AsrError as exc:
        if exc.status_code.startswith("5") or "429" in exc.status_code:
            terminal = await _requeue_or_timeout(runtime, claimed)
            if terminal is not None:
                return terminal
            raise RecordingTaskPending(str(claimed.id)) from exc
        return await _fail_job(
            runtime,
            claimed,
            JobErrorCode.PROVIDER_ERROR,
            "speech recognition failed",
        )
    except RecordingModelOutputError:
        return await _fail_job(
            runtime,
            claimed,
            JobErrorCode.PROVIDER_ERROR,
            "recording analysis failed",
        )
    except (RecordingStateError, KeyError, TypeError, ValueError):
        return await _fail_job(
            runtime,
            claimed,
            JobErrorCode.INVALID_INPUT,
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
        return await _fail_job(
            runtime,
            claimed,
            JobErrorCode.INTERNAL_ERROR,
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


@celery_app.task(
    bind=True,
    name="tasks.recording_tasks.process_recording",
    ignore_result=True,
)
def process_recording(self, schema_version: int, job_id: str, job_type: str) -> dict:
    try:
        return asyncio.run(
            run_recording_job(
                settings,
                schema_version=schema_version,
                job_id=job_id,
                job_type=job_type,
            )
        )
    except RecordingTaskPending as exc:
        raise self.retry(exc=exc, countdown=POLL_INTERVAL_SECONDS, max_retries=None)
