import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from api import jobs as jobs_api
from services.jobs.types import JobErrorCode, JobRecord, JobStatus


OWNER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_OWNER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
JOB_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _job(owner_id=OWNER_ID, status=JobStatus.PENDING):
    created_at = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
    return JobRecord(
        id=JOB_ID,
        owner_id=owner_id,
        job_type="interview.finish",
        status=status,
        idempotency_key="safe-key",
        payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
        result_ref=None,
        attempt=1,
        max_attempts=5,
        created_at=created_at,
        updated_at=created_at,
        started_at=None,
        finished_at=None,
        lease_expires_at=None,
        error_code=None,
    )


async def test_get_job_returns_frozen_public_shape(monkeypatch):
    async def load(owner_id, job_id):
        assert owner_id == str(OWNER_ID)
        assert job_id == str(JOB_ID)
        return _job()

    monkeypatch.setattr(jobs_api, "load_owned_job", load)

    result = await jobs_api.get_job(str(JOB_ID), {"id": str(OWNER_ID)})

    assert result == {
        "job_id": str(JOB_ID),
        "type": "interview.finish",
        "status": "pending",
        "attempt": 1,
        "created_at": "2026-08-26T08:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "result_ref": None,
        "error_code": None,
    }
    assert "payload_ref" not in result
    assert "owner_id" not in result


@pytest.mark.parametrize("job_id", [str(JOB_ID), "not-a-uuid"])
async def test_get_job_hides_unknown_and_cross_owner_as_404(monkeypatch, job_id):
    async def load(_owner_id, _job_id):
        return None

    monkeypatch.setattr(jobs_api, "load_owned_job", load)

    with pytest.raises(HTTPException) as error:
        await jobs_api.get_job(job_id, {"id": str(OTHER_OWNER_ID)})

    assert error.value.status_code == 404
    assert error.value.detail == "任务不存在"


async def test_failed_job_exposes_only_public_error_code(monkeypatch):
    pending = _job(status=JobStatus.FAILED)
    failed = replace(
        pending,
        finished_at=pending.created_at,
        error_code=JobErrorCode.INTERNAL_ERROR,
    )

    async def load(_owner_id, _job_id):
        return failed

    monkeypatch.setattr(jobs_api, "load_owned_job", load)
    result = await jobs_api.get_job(str(JOB_ID), {"id": str(OWNER_ID)})

    assert result["error_code"] == "INTERNAL_ERROR"
    assert set(result) == {
        "job_id",
        "type",
        "status",
        "attempt",
        "created_at",
        "started_at",
        "finished_at",
        "result_ref",
        "error_code",
    }
