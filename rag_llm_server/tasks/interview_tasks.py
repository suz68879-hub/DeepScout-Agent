import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from agents.graph import ColdPathOutputError, close_graph, init_graph
from config import Config, settings
from db import close_database, get_database_runtime, init_database
from db.engine import DatabaseRuntime
from db.models import InterviewReport, InterviewSession
from middleware.request_context import job_consumer_span
from services.interview_service import ColdPathStateError, run_cold_path
from services.jobs.handlers import JobType
from services.jobs.repository import JobRepository
from services.jobs.types import JobErrorCode, JobRecord, JobStatus
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
ColdRunner = Callable[[JobRecord], Awaitable[dict]]


class InterviewTaskMessageError(Exception):
    """消息没有匹配已持久化的面试任务。"""


class PreviousInterviewJobPending(Exception):
    """同一会话更早的冷任务尚未完成。"""


class InterviewTaskPending(Exception):
    """A retryable cold-path failure with a persisted retry countdown."""

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
        or job_type != JobType.INTERVIEW_FINISH.value
    ):
        raise InterviewTaskMessageError("INVALID_INTERVIEW_TASK_MESSAGE")
    try:
        return uuid.UUID(job_id)
    except (TypeError, ValueError, AttributeError):
        raise InterviewTaskMessageError("INVALID_INTERVIEW_TASK_MESSAGE") from None


def _validate_result(result_ref: dict, session_id: str) -> dict:
    if (
        not isinstance(result_ref, dict)
        or frozenset(result_ref) - {"schema_version", "session_id", "report_id"}
        or type(result_ref.get("schema_version")) is not int
        or result_ref.get("schema_version") != 1
        or result_ref.get("session_id") != session_id
    ):
        raise ColdPathStateError("INVALID_COLD_PATH_RESULT")
    if "report_id" in result_ref:
        try:
            uuid.UUID(result_ref["report_id"])
        except (TypeError, ValueError, AttributeError):
            raise ColdPathStateError("INVALID_COLD_PATH_RESULT") from None
    return dict(result_ref)


async def _existing_report(session, job: JobRecord) -> InterviewReport | None:
    try:
        session_id = uuid.UUID(job.payload_ref["session_id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise InterviewTaskMessageError("INVALID_INTERVIEW_JOB_PAYLOAD") from None
    interview = await session.scalar(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.user_id == job.owner_id,
        )
    )
    if interview is None:
        raise InterviewTaskMessageError("INVALID_INTERVIEW_JOB_PAYLOAD")
    return await session.scalar(
        select(InterviewReport).where(
            InterviewReport.session_id == session_id,
            InterviewReport.user_id == job.owner_id,
        )
    )


async def _resolve_failure(
    runtime: DatabaseRuntime,
    job: JobRecord,
    failure: FailureClassification,
) -> dict:
    decision = decide_retry(
        failure,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
    )
    async with runtime.session_scope() as session:
        resolved = await JobRepository(session).resolve_failure(
            job.id,
            decision.error_code,
            retryable=decision.should_retry,
        )
    if resolved.status is JobStatus.PENDING:
        raise InterviewTaskPending(str(job.id), decision.countdown)
    logger.warning(
        "Interview cold-path job failed",
        extra={
            "event": "interview_cold_job_failed",
            "job_id": str(job.id),
            "error_code": resolved.error_code.value,
        },
    )
    return _summary(resolved)


async def execute_interview_job(
    runtime: DatabaseRuntime,
    *,
    schema_version: int,
    job_id: str,
    job_type: str,
    cold_runner: ColdRunner = run_cold_path,
) -> dict:
    """执行一条已持久化消息；所有状态迁移都在独立短事务中完成。"""
    locked_job_id = _validate_message(schema_version, job_id, job_type)
    retry_countdown = None
    async with runtime.session_scope() as session:
        repository = JobRepository(session)
        job = await repository.get_internal(locked_job_id)
        if job is None or job.job_type != JobType.INTERVIEW_FINISH.value:
            raise InterviewTaskMessageError("INTERVIEW_JOB_NOT_FOUND")
        if job.status is JobStatus.RUNNING:
            recovered = await repository.recover_expired(job.id)
            if recovered is None:
                retry_countdown = 5
            elif recovered.status is JobStatus.PENDING:
                retry_countdown = countdown_for_retry(recovered.attempt)
            if recovered is not None:
                job = recovered
        if retry_countdown is None and job.status is not JobStatus.PENDING:
            return _summary(job)
        if retry_countdown is None:
            try:
                uuid.UUID(job.payload_ref["session_id"])
            except (KeyError, TypeError, ValueError, AttributeError):
                raise InterviewTaskMessageError(
                    "INVALID_INTERVIEW_JOB_PAYLOAD"
                ) from None
            if await repository.has_unfinished_predecessor(job):
                raise PreviousInterviewJobPending(str(job.id))
            claimed = await repository.claim(
                job.id,
                lease_duration=timedelta(minutes=5),
            )
    if retry_countdown is not None:
        raise InterviewTaskPending(str(job.id), retry_countdown)

    try:
        async with runtime.session_scope() as session:
            report = await _existing_report(session, claimed)
            if report is not None:
                interview = await session.get(
                    InterviewSession,
                    uuid.UUID(claimed.payload_ref["session_id"]),
                )
                if interview is None:
                    raise InterviewTaskMessageError("INVALID_INTERVIEW_JOB_PAYLOAD")
                interview.status = "finished"
                interview.stage = "finish"
                interview.ended_at = interview.ended_at or datetime.now(timezone.utc)
                succeeded = await JobRepository(session).succeed(
                    claimed.id,
                    result_ref={
                        "schema_version": 1,
                        "session_id": claimed.payload_ref["session_id"],
                        "report_id": str(report.id),
                    },
                )
                return _summary(succeeded)

        result_ref = _validate_result(
            await cold_runner(claimed), claimed.payload_ref["session_id"]
        )
    except ColdPathOutputError:
        return await _resolve_failure(
            runtime,
            claimed,
            FailureClassification(JobErrorCode.PROVIDER_ERROR, False),
        )
    except (ColdPathStateError, InterviewTaskMessageError):
        return await _resolve_failure(
            runtime,
            claimed,
            FailureClassification(JobErrorCode.INVALID_INPUT, False),
        )
    except (httpx.RequestError, OSError) as exc:
        failure = (
            FailureClassification(JobErrorCode.NETWORK_ERROR, True)
            if isinstance(exc, httpx.RequestError)
            else classify_exception(exc)
        )
        try:
            return await _resolve_failure(runtime, claimed, failure)
        except InterviewTaskPending as pending:
            raise pending from exc
    except Exception as exc:
        logger.error(
            "Interview cold-path job raised an internal error",
            extra={
                "event": "interview_cold_job_internal_error",
                "job_id": str(claimed.id),
                "error_code": JobErrorCode.INTERNAL_ERROR.value,
                "error_type": type(exc).__name__,
            },
        )
        return await _resolve_failure(
            runtime,
            claimed,
            FailureClassification(JobErrorCode.INTERNAL_ERROR, False),
        )

    async with runtime.session_scope() as session:
        succeeded = await JobRepository(session).succeed(
            claimed.id,
            result_ref=result_ref,
        )
    logger.info(
        "Interview cold-path job succeeded",
        extra={
            "event": "interview_cold_job_succeeded",
            "job_id": str(claimed.id),
        },
    )
    return _summary(succeeded)


async def run_interview_job(
    config: Config,
    *,
    schema_version: int,
    job_id: str,
    job_type: str,
) -> dict:
    await init_database(config)
    try:
        await init_storage()
        await init_graph()
        return await execute_interview_job(
            get_database_runtime(),
            schema_version=schema_version,
            job_id=job_id,
            job_type=job_type,
        )
    finally:
        try:
            await close_graph()
        finally:
            try:
                await close_storage()
            finally:
                await close_database()


def _finish_interview(self, schema_version: int, job_id: str, job_type: str) -> dict:
    try:
        result = asyncio.run(
            run_interview_job(
                settings,
                schema_version=schema_version,
                job_id=job_id,
                job_type=job_type,
            )
        )
    except PreviousInterviewJobPending as exc:
        raise self.retry(exc=exc, countdown=5, max_retries=None)
    except InterviewTaskPending as exc:
        raise self.retry(exc=exc, countdown=exc.countdown, max_retries=None)
    if result.get("status") == JobStatus.FAILED.value:
        DeadLetterPublisher(celery_app).publish(
            job_id=result["job_id"],
            error_code=result["error_code"],
            original_queue="deepscout.cold",
        )
    return result


@celery_app.task(
    bind=True,
    name="tasks.interview_tasks.finish_interview",
    ignore_result=True,
)
def finish_interview(self, schema_version: int, job_id: str, job_type: str) -> dict:
    headers = getattr(self.request, "headers", None)
    with job_consumer_span(
        headers=headers,
        job_id=job_id,
        operation=JobType.INTERVIEW_FINISH.value,
    ):
        return _finish_interview(self, schema_version, job_id, job_type)
