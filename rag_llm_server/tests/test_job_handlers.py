import pytest

from services.jobs.handlers import (
    JobHandlerError,
    JobHandlerRegistry,
    JobType,
)
from services.jobs.types import JobErrorCode


async def test_registry_resolves_each_locked_job_type():
    async def interview_handler(job):
        return {"handler": "interview", "job_id": str(job.id)}

    async def recording_handler(job):
        return {"handler": "recording", "job_id": str(job.id)}

    registry = JobHandlerRegistry(
        {
            JobType.INTERVIEW_FINISH: interview_handler,
            JobType.RECORDING_PROCESS: recording_handler,
        }
    )

    assert registry.resolve(JobType.INTERVIEW_FINISH) is interview_handler
    assert registry.resolve(JobType.RECORDING_PROCESS) is recording_handler


def test_registry_rejects_missing_handler():
    registry = JobHandlerRegistry({})

    with pytest.raises(KeyError, match="handler is not configured"):
        registry.resolve(JobType.INTERVIEW_FINISH)


def test_handler_error_exposes_only_public_error_code():
    error = JobHandlerError(JobErrorCode.INVALID_INPUT)

    assert error.error_code is JobErrorCode.INVALID_INPUT
    assert str(error) == "INVALID_INPUT"
    with pytest.raises(ValueError):
        JobHandlerError("password=secret\ninternal stack")
