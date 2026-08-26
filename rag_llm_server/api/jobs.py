"""Owner-scoped public API for durable background jobs."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from db import session_scope
from services.jobs.repository import JobRepository
from services.jobs.types import JobRecord, JobStatus

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobResponse(BaseModel):
    job_id: str
    type: str
    status: JobStatus
    attempt: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    result_ref: dict[str, Any] | None
    error_code: str | None


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def load_owned_job(owner_id: str, job_id: str) -> JobRecord | None:
    try:
        owner_uuid = uuid.UUID(str(owner_id))
        job_uuid = uuid.UUID(job_id)
    except (TypeError, ValueError, AttributeError):
        return None
    async with session_scope() as db_session:
        return await JobRepository(db_session).get(owner_uuid, job_uuid)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, user: dict = Depends(get_current_user)):
    job = await load_owned_job(user["id"], job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "job_id": str(job.id),
        "type": job.job_type,
        "status": job.status.value,
        "attempt": job.attempt,
        "created_at": _timestamp(job.created_at),
        "started_at": _timestamp(job.started_at),
        "finished_at": _timestamp(job.finished_at),
        "result_ref": job.result_ref,
        "error_code": job.error_code.value if job.error_code else None,
    }
