from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum

from services.jobs.types import JobErrorCode, JobRecord


class JobType(StrEnum):
    INTERVIEW_FINISH = "interview.finish"
    RECORDING_PROCESS = "recording.process"


JobHandler = Callable[[JobRecord], Awaitable[dict]]


class JobHandlerError(Exception):
    """A handler failure safe to persist as a public error code."""

    def __init__(self, error_code: JobErrorCode | str) -> None:
        self.error_code = JobErrorCode(error_code)
        super().__init__(self.error_code.value)


class JobHandlerRegistry:
    def __init__(self, handlers: Mapping[JobType, JobHandler]) -> None:
        self._handlers = dict(handlers)

    def resolve(self, job_type: JobType) -> JobHandler:
        try:
            return self._handlers[job_type]
        except KeyError:
            raise KeyError(f"handler is not configured: {job_type.value}") from None
