import itertools

import pytest

from services.jobs.types import (
    JobErrorCode,
    JobStatus,
    can_transition,
)


ALLOWED_TRANSITIONS = {
    (JobStatus.PENDING, JobStatus.RUNNING),
    (JobStatus.PENDING, JobStatus.CANCELLED),
    (JobStatus.RUNNING, JobStatus.PENDING),
    (JobStatus.RUNNING, JobStatus.SUCCEEDED),
    (JobStatus.RUNNING, JobStatus.FAILED),
    (JobStatus.RUNNING, JobStatus.CANCELLED),
}


@pytest.mark.parametrize(
    ("source", "target"),
    list(itertools.product(JobStatus, repeat=2)),
)
def test_state_matrix_allows_only_locked_transitions(source, target):
    assert can_transition(source, target) is ((source, target) in ALLOWED_TRANSITIONS)


def test_public_error_codes_reject_internal_exception_text():
    assert JobErrorCode("WORKER_LOST") is JobErrorCode.WORKER_LOST
    with pytest.raises(ValueError):
        JobErrorCode("connection failed: password=secret\ninternal stack trace")
