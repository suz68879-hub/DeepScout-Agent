"""Durable recording-analysis orchestration."""

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agents.recording_analyzer import (
    candidate_segments,
    generate_recording_report,
    judge_roles,
    label_roles,
)
from db import session_scope
from services.asr_client import parse_transcript, query_asr, submit_asr
from services.clock import utc_now
from services.jobs.dispatcher import JobDispatcher
from services.jobs.handlers import JobType
from services.jobs.types import JobRecord
from services.report_service import save_recording_report
from services.storage import get_tos_store, storage
from services.storage.postgres import PostgresRepository

POLL_INTERVAL_SECONDS = 10
ALLOWED_EXTS = {"mp3", "wav", "ogg"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
DEFAULT_POSITION = "真实面试录音"


class RecordingStateError(Exception):
    """The persisted recording does not match the durable job."""


class RecordingPollPending(Exception):
    """ASR has not completed; the durable task should be retried later."""


class RecordingModelOutputError(Exception):
    """The analysis provider returned no usable recording result."""


async def create_recording_job(
    db_session: AsyncSession,
    *,
    owner_id: uuid.UUID | str,
    recording: dict,
) -> tuple[dict, JobRecord]:
    """Atomically persist a recording, durable Job, and Outbox event."""
    try:
        owner_uuid = uuid.UUID(str(owner_id))
        recording_uuid = uuid.UUID(str(recording.get("id", "")))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("INVALID_RECORDING_REFERENCE") from None
    tos_key = recording.get("tos_key")
    if not isinstance(tos_key, str) or not tos_key:
        raise ValueError("INVALID_RECORDING_REFERENCE")
    row = await PostgresRepository(db_session).recording_create(
        str(owner_uuid), {**recording, "id": str(recording_uuid)}
    )
    job = await JobDispatcher(db_session).enqueue(
        job_type=JobType.RECORDING_PROCESS,
        owner_id=owner_uuid,
        payload_ref={
            "schema_version": 1,
            "recording_id": str(recording_uuid),
            "tos_key": tos_key,
        },
        idempotency_key=f"recording:{recording_uuid}:process",
    )
    return row, job


async def enqueue_uploaded_recording(
    owner_id: uuid.UUID | str, recording: dict
) -> tuple[dict, JobRecord]:
    async with session_scope() as db_session:
        return await create_recording_job(
            db_session, owner_id=owner_id, recording=recording
        )


def ensure_tos():
    store = get_tos_store()
    if store is None:
        raise RuntimeError("recording analysis requires TOS configuration")
    return store


async def upload_recording(
    user_id: str,
    filename: str,
    ext: str,
    raw: bytes,
    position: str,
) -> dict:
    store = ensure_tos()
    recording_id = str(uuid.uuid4())
    tos_key = f"users/{user_id}/recordings/{recording_id}.{ext}"
    await store.save_bytes(tos_key, raw)
    row, job = await enqueue_uploaded_recording(
        user_id,
        {
            "id": recording_id,
            "filename": filename,
            "ext": ext,
            "tos_key": tos_key,
            "size_bytes": len(raw),
            "position": position,
            "status": "processing",
            "asr_task_id": None,
        },
    )
    return {**row, "job_id": str(job.id)}


def _recording_reference(job: JobRecord) -> tuple[str, str]:
    payload = job.payload_ref
    if not isinstance(payload, dict) or frozenset(payload) != {
        "schema_version",
        "recording_id",
        "tos_key",
    }:
        raise RecordingStateError("INVALID_RECORDING_JOB_PAYLOAD")
    if payload.get("schema_version") != 1:
        raise RecordingStateError("INVALID_RECORDING_JOB_PAYLOAD")
    try:
        recording_id = str(uuid.UUID(payload["recording_id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RecordingStateError("INVALID_RECORDING_JOB_PAYLOAD") from None
    tos_key = payload.get("tos_key")
    if not isinstance(tos_key, str) or not tos_key:
        raise RecordingStateError("INVALID_RECORDING_JOB_PAYLOAD")
    return recording_id, tos_key


def _asr_task_id(recording_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"deepscout:recording:{recording_id}"))


async def process_recording(job: JobRecord) -> dict:
    """Perform one bounded processing step; never sleep or poll in a loop."""
    recording_id, tos_key = _recording_reference(job)
    row = await storage.recording_get_internal(recording_id)
    if (
        row is None
        or str(row.get("user_id")) != str(job.owner_id)
        or row.get("tos_key") != tos_key
    ):
        raise RecordingStateError("INVALID_RECORDING_REFERENCE")

    report_id = row.get("report_id")
    if row.get("status") == "done" and report_id:
        return {
            "schema_version": 1,
            "recording_id": recording_id,
            "report_id": str(report_id),
        }
    if row.get("status") != "processing":
        raise RecordingStateError("INVALID_RECORDING_STATE")

    existing_report = await storage.report_get(str(job.owner_id), recording_id)
    if existing_report is not None:
        await storage.recording_update(
            str(job.owner_id),
            recording_id,
            {
                "status": "done",
                "report_id": existing_report["id"],
                "finished_at": utc_now(),
            },
        )
        return {
            "schema_version": 1,
            "recording_id": recording_id,
            "report_id": str(existing_report["id"]),
        }

    store = ensure_tos()
    asr_task_id = row.get("asr_task_id")
    if not asr_task_id:
        asr_task_id = _asr_task_id(recording_id)
        submitted_id = await submit_asr(
            store.presigned_url(tos_key, expires=3600),
            row["ext"],
            task_id=asr_task_id,
        )
        if submitted_id != asr_task_id:
            raise RecordingStateError("INVALID_ASR_TASK_ID")
        await storage.recording_update(
            str(job.owner_id), recording_id, {"asr_task_id": asr_task_id}
        )

    payload = await query_asr(asr_task_id)
    if payload is None:
        raise RecordingPollPending(recording_id)

    transcript = parse_transcript(payload)
    try:
        assignment = await judge_roles(transcript)
        segments = candidate_segments(transcript, assignment)
        if not segments:
            raise RecordingModelOutputError("NO_CANDIDATE_SEGMENTS")
        report = await generate_recording_report(
            segments, row.get("position") or DEFAULT_POSITION
        )
    except RecordingModelOutputError:
        raise
    except Exception as exc:
        raise RecordingModelOutputError("INVALID_MODEL_OUTPUT") from exc

    labeled = label_roles(transcript, assignment)
    report_id = await save_recording_report(
        recording_id,
        report.model_dump(),
        row.get("position") or DEFAULT_POSITION,
        labeled,
        assignment.model_dump(),
    )
    await storage.recording_update(
        str(job.owner_id),
        recording_id,
        {
            "status": "done",
            "report_id": report_id,
            "transcript_json": json.dumps(labeled, ensure_ascii=False),
            "finished_at": utc_now(),
        },
    )
    return {
        "schema_version": 1,
        "recording_id": recording_id,
        "report_id": str(report_id),
    }
