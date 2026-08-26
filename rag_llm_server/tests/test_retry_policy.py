import uuid

import pytest

from services.jobs.types import JobErrorCode
from tasks.retry_policy import (
    DEAD_LETTER_QUEUE,
    RETRY_BACKOFF_SECONDS,
    DeadLetterPublisher,
    FailureClassification,
    build_dead_letter_message,
    classify_exception,
    decide_retry,
)


def test_retry_backoff_is_locked_to_phase_contract():
    assert RETRY_BACKOFF_SECONDS == (5, 30, 120, 600, 1800)


@pytest.mark.parametrize(
    ("error_code", "retryable"),
    [
        (JobErrorCode.NETWORK_ERROR, True),
        (JobErrorCode.RATE_LIMITED, True),
        (JobErrorCode.PROVIDER_ERROR, True),
        (JobErrorCode.WORKER_LOST, True),
        (JobErrorCode.INVALID_INPUT, False),
        (JobErrorCode.PERMISSION_DENIED, False),
        (JobErrorCode.SECURITY_ERROR, False),
        (JobErrorCode.INTERNAL_ERROR, False),
    ],
)
def test_failure_categories_have_explicit_retry_behavior(error_code, retryable):
    decision = decide_retry(
        FailureClassification(error_code, retryable=retryable),
        attempt=0,
        max_attempts=5,
    )

    assert decision.should_retry is retryable
    assert decision.error_code is error_code
    assert decision.countdown == (5 if retryable else None)


@pytest.mark.parametrize(
    ("attempt", "countdown"), tuple(enumerate(RETRY_BACKOFF_SECONDS))
)
def test_retry_delay_uses_persisted_attempt(attempt, countdown):
    decision = decide_retry(
        FailureClassification(JobErrorCode.NETWORK_ERROR, retryable=True),
        attempt=attempt,
        max_attempts=5,
    )

    assert decision.should_retry is True
    assert decision.countdown == countdown


def test_retry_budget_exhaustion_is_terminal():
    decision = decide_retry(
        FailureClassification(JobErrorCode.NETWORK_ERROR, retryable=True),
        attempt=5,
        max_attempts=5,
    )

    assert decision.should_retry is False
    assert decision.countdown is None
    assert decision.error_code is JobErrorCode.MAX_ATTEMPTS_EXCEEDED


@pytest.mark.parametrize(
    ("error", "error_code", "retryable"),
    [
        (PermissionError("denied"), JobErrorCode.PERMISSION_DENIED, False),
        (ValueError("bad input"), JobErrorCode.INVALID_INPUT, False),
        (OSError("network down"), JobErrorCode.NETWORK_ERROR, True),
        (RuntimeError("unknown"), JobErrorCode.INTERNAL_ERROR, False),
    ],
)
def test_exception_classification_defaults_unknown_to_terminal(
    error, error_code, retryable
):
    assert classify_exception(error) == FailureClassification(
        error_code,
        retryable=retryable,
    )


def test_dead_letter_message_contains_only_allowlisted_non_pii_fields():
    job_id = uuid.uuid4()

    message = build_dead_letter_message(
        job_id=job_id,
        error_code=JobErrorCode.MAX_ATTEMPTS_EXCEEDED,
        original_queue="deepscout.recording",
    )

    assert message == {
        "job_id": str(job_id),
        "error_code": "MAX_ATTEMPTS_EXCEEDED",
        "original_queue": "deepscout.recording",
    }


def test_dead_letter_message_rejects_unknown_queue():
    with pytest.raises(ValueError, match="original_queue"):
        build_dead_letter_message(
            job_id=uuid.uuid4(),
            error_code=JobErrorCode.INTERNAL_ERROR,
            original_queue="attacker.queue",
        )


def test_dead_letter_publisher_uses_persistent_confirmed_json_message():
    calls = []

    class ProducerContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def publish(self, message, **options):
            calls.append((message, options))

    class FakeApp:
        def producer_or_acquire(self):
            return ProducerContext()

    job_id = uuid.uuid4()
    DeadLetterPublisher(FakeApp()).publish(
        job_id=job_id,
        error_code=JobErrorCode.INTERNAL_ERROR,
        original_queue="deepscout.cold",
    )

    message, options = calls[0]
    assert message == {
        "job_id": str(job_id),
        "error_code": "INTERNAL_ERROR",
        "original_queue": "deepscout.cold",
    }
    assert options["routing_key"] == "failed"
    assert options["serializer"] == "json"
    assert options["delivery_mode"] == 2
    assert options["mandatory"] is True
    assert options["retry"] is True
    assert [queue.name for queue in options["declare"]] == [DEAD_LETTER_QUEUE]
