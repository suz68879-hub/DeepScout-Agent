"""Locked retry policy and privacy-safe dead-letter messages for job workers."""

import uuid
from dataclasses import dataclass

from kombu import Exchange, Queue

from services.jobs.types import JobErrorCode

RETRY_BACKOFF_SECONDS = (5, 30, 120, 600, 1800)
DEAD_LETTER_EXCHANGE = "deepscout.dead_letter"
DEAD_LETTER_QUEUE = "deepscout.dlq"
_DEAD_LETTER_SOURCE_QUEUES = frozenset(
    {"deepscout.cold", "deepscout.recording"}
)


@dataclass(frozen=True, slots=True)
class FailureClassification:
    error_code: JobErrorCode
    retryable: bool


@dataclass(frozen=True, slots=True)
class RetryDecision:
    should_retry: bool
    error_code: JobErrorCode
    countdown: int | None


def classify_exception(error: Exception) -> FailureClassification:
    """Classify generic exceptions conservatively; unknown errors are terminal."""
    if isinstance(error, PermissionError):
        return FailureClassification(JobErrorCode.PERMISSION_DENIED, False)
    if isinstance(error, (ValueError, TypeError)):
        return FailureClassification(JobErrorCode.INVALID_INPUT, False)
    if isinstance(error, OSError):
        return FailureClassification(JobErrorCode.NETWORK_ERROR, True)
    return FailureClassification(JobErrorCode.INTERNAL_ERROR, False)


def decide_retry(
    failure: FailureClassification,
    *,
    attempt: int,
    max_attempts: int,
) -> RetryDecision:
    if not 0 <= attempt <= max_attempts <= len(RETRY_BACKOFF_SECONDS):
        raise ValueError("invalid persisted retry count")
    if not failure.retryable:
        return RetryDecision(False, failure.error_code, None)
    if attempt >= max_attempts:
        return RetryDecision(False, JobErrorCode.MAX_ATTEMPTS_EXCEEDED, None)
    return RetryDecision(True, failure.error_code, RETRY_BACKOFF_SECONDS[attempt])


def countdown_for_retry(attempt: int) -> int:
    """Return the locked delay for a persisted retry number (1 through 5)."""
    if not 1 <= attempt <= len(RETRY_BACKOFF_SECONDS):
        raise ValueError("invalid persisted retry count")
    return RETRY_BACKOFF_SECONDS[attempt - 1]


def build_dead_letter_message(
    *,
    job_id: uuid.UUID | str,
    error_code: JobErrorCode | str,
    original_queue: str,
) -> dict[str, str]:
    if original_queue not in _DEAD_LETTER_SOURCE_QUEUES:
        raise ValueError("invalid original_queue")
    return {
        "job_id": str(job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(job_id)),
        "error_code": JobErrorCode(error_code).value,
        "original_queue": original_queue,
    }


class DeadLetterPublisher:
    """Publish one persistent, allowlisted message without job payload data."""

    def __init__(self, app) -> None:
        self._app = app

    def publish(
        self,
        *,
        job_id: uuid.UUID | str,
        error_code: JobErrorCode | str,
        original_queue: str,
    ) -> None:
        message = build_dead_letter_message(
            job_id=job_id,
            error_code=error_code,
            original_queue=original_queue,
        )
        exchange = Exchange(DEAD_LETTER_EXCHANGE, type="direct", durable=True)
        queue = Queue(
            DEAD_LETTER_QUEUE,
            exchange=exchange,
            routing_key="failed",
            durable=True,
        )
        with self._app.producer_or_acquire() as producer:
            producer.publish(
                message,
                exchange=exchange,
                routing_key="failed",
                serializer="json",
                delivery_mode=2,
                declare=[queue],
                mandatory=True,
                retry=True,
            )
