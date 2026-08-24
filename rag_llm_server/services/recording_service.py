"""Single-process recording analysis pipeline with explicit row ownership."""
import json
import logging
import time
import uuid
from asyncio import create_task, sleep

from agents.recording_analyzer import (
    candidate_segments,
    generate_recording_report,
    judge_roles,
    label_roles,
)
from services.asr_client import AsrError, parse_transcript, query_asr, submit_asr
from services.clock import utc_now
from services.report_service import save_recording_report
from services.storage import get_tos_store, storage

logger = logging.getLogger("recording")

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 30 * 60
ALLOWED_EXTS = {"mp3", "wav", "ogg"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
DEFAULT_POSITION = "真实面试录音"

_running_tasks: set[str] = set()


def ensure_tos():
    store = get_tos_store()
    if store is None:
        raise RuntimeError("recording analysis requires TOS configuration")
    return store


def schedule_recording(recording_id: str, position: str = DEFAULT_POSITION) -> None:
    if recording_id in _running_tasks:
        return
    _running_tasks.add(recording_id)
    create_task(_process_recording(recording_id, position))


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
    await storage.recording_create(user_id, {
        "id": recording_id,
        "filename": filename,
        "ext": ext,
        "tos_key": tos_key,
        "size_bytes": len(raw),
        "status": "processing",
    })
    try:
        task_id = await submit_asr(store.presigned_url(tos_key, expires=3600), ext)
    except Exception:
        await _fail(user_id, recording_id, "speech recognition task submission failed")
        raise
    await storage.recording_update(user_id, recording_id, {"asr_task_id": task_id})
    schedule_recording(recording_id, position)
    return await storage.recording_get(user_id, recording_id)


async def _process_recording(recording_id: str, position: str) -> None:
    transcript = None
    initial = await storage.recording_get_internal(recording_id)
    if initial is None:
        _running_tasks.discard(recording_id)
        return
    user_id = initial["user_id"]
    try:
        started = time.monotonic()
        payload = None
        while payload is None:
            await sleep(POLL_INTERVAL_SECONDS)
            row = await storage.recording_get(user_id, recording_id)
            if row is None or row["status"] != "processing":
                return
            if time.monotonic() - started > POLL_TIMEOUT_SECONDS:
                await _fail(user_id, recording_id, "transcription timed out")
                return
            try:
                payload = await query_asr(row["asr_task_id"])
            except AsrError:
                await _fail(user_id, recording_id, "speech recognition failed")
                return
        transcript = parse_transcript(payload)
        assignment = await judge_roles(transcript)
        segments = candidate_segments(transcript, assignment)
        if not segments:
            await _fail(user_id, recording_id, "no candidate speech was detected")
            return
        report = await generate_recording_report(segments, position)
        labeled = label_roles(transcript, assignment)
        report_id = await save_recording_report(
            recording_id,
            report.model_dump(),
            position,
            labeled,
            assignment.model_dump(),
        )
        await storage.recording_update(user_id, recording_id, {
            "status": "done",
            "report_id": report_id,
            "transcript_json": json.dumps(labeled, ensure_ascii=False),
            "finished_at": utc_now(),
        })
    except Exception as exc:
        logger.error(
            "Recording processing failed recording_id=%s error_type=%s",
            recording_id,
            type(exc).__name__,
        )
        patch = {
            "status": "failed",
            "error": "recording analysis failed",
            "finished_at": utc_now(),
        }
        if transcript:
            patch["transcript_json"] = json.dumps(transcript, ensure_ascii=False)
        await storage.recording_update(user_id, recording_id, patch)
    finally:
        _running_tasks.discard(recording_id)


async def _fail(user_id: str, recording_id: str, error: str) -> None:
    await storage.recording_update(user_id, recording_id, {
        "status": "failed", "error": error, "finished_at": utc_now(),
    })


async def resume_pending() -> list[str]:
    resumed = []
    for row in await storage.recording_list_processing():
        if row.get("asr_task_id"):
            schedule_recording(row["id"])
            resumed.append(row["id"])
        else:
            await _fail(
                row["user_id"], row["id"], "task was interrupted before ASR submission",
            )
    return resumed
