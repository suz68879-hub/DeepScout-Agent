"""会话编排：graph 的 HTTP 适配层（spec §5.5）。

热路径：定位/创建会话 → 写入用户消息 → 触发 interviewer 节点（占位）→
HTTP 层流式生成 → 回写助手消息。冷路径（评分/出题/报告）见文件尾 Task 13 段。
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from agents.graph import get_graph
from agents.prompts.registry import registry
from config import settings
from db import session_scope
from services.clock import utc_now
from services.jobs.dispatcher import JobDispatcher
from services.jobs.handlers import JobType
from services.jobs.repository import JobRepository
from services.jobs.types import JobRecord, JobStatus
from services.storage import storage
from services.storage.base import StorageConflictError, StorageVersionConflictError
from services.storage.postgres import PostgresRepository

DEFAULT_POSITION = "Java后端"
HOT_PATH_COLD_WAIT_SECONDS = 5.0
READINESS_PROMPT = "你好，我是今天的面试官懂小智。正式开始前，请问你准备好了吗？"
SELF_INTRO_PROMPT = "好的，下面请进行一分钟左右的自我介绍。"
WAITING_PROMPT = "好的，你准备好后告诉我“我准备好了”，我们再开始。"
logger = logging.getLogger(__name__)


def build_welcome_message(state: dict) -> str:
    """RTC 入房后主动播报的开场白；不调用 LLM，避免首屏等待。"""
    position = state.get("position") or DEFAULT_POSITION
    return f"你好，我是今天的面试官懂小智，负责你的{position}面试。正式开始前，请问你准备好了吗？"


def _message_content(message: object) -> str:
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(getattr(message, "content", ""))


def readiness_gate_reply(state: dict, user_text: str) -> str | None:
    """在 intro 首轮增加确定性的准备确认，不把确认语误当作自我介绍。"""
    if state.get("stage") != "intro" or state.get("round_no", 0) != 0:
        return None
    if any(SELF_INTRO_PROMPT in _message_content(message) for message in state.get("messages", [])):
        return None

    normalized = "".join(str(user_text).lower().split())
    negative_phrases = ("没准备好", "没有准备好", "还没", "等一下", "稍等", "等等")
    if any(phrase in normalized for phrase in negative_phrases):
        return WAITING_PROMPT
    ready_phrases = ("准备好了", "我准备好", "可以开始", "开始吧", "ready")
    return SELF_INTRO_PROMPT if any(phrase in normalized for phrase in ready_phrases) else WAITING_PROMPT


def session_config(session_id: str) -> dict:
    """LangGraph config：thread_id = session_id（spec §5.1）。"""
    return {"configurable": {"thread_id": session_id}}


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def session_expired(
    session: dict,
    *,
    now: datetime | str | None = None,
    max_seconds: int | None = None,
) -> bool:
    """running 会话超过 SESSION_MAX_SECONDS 视为过期，需回收。"""
    if session.get("status") != "running":
        return False
    started = _parse_dt(session.get("started_at"))
    if started is None:
        return False
    current = _parse_dt(now) if now is not None else datetime.now(timezone.utc)
    if current is None:
        current = datetime.now(timezone.utc)
    limit = settings.SESSION_MAX_SECONDS if max_seconds is None else max_seconds
    return (current - started).total_seconds() > limit


async def _stop_voice_chat_best_effort(session: dict) -> None:
    try:
        from services.rtc_service import call_voice_chat_openapi

        await call_voice_chat_openapi("StopVoiceChat", "2024-12-01", session, {})
    except Exception:
        logger.warning(
            "StopVoiceChat during session reclaim failed",
            extra={"event": "interview_reclaim_stop_failed", "session_id": session.get("id")},
        )


async def abandon_session(
    user_id: str,
    session_id: str,
    session: dict | None = None,
) -> dict | None:
    """结束 running 会话但不生成报告（关页 / TTL / 开新场回收）。

    以库中当前行为准做 CAS，调用方传入的 snapshot 不能覆盖 finished。
    """
    current = await storage.session_get(user_id, session_id)
    if current is None:
        return None
    if current.get("status") != "running":
        return current
    rtc_ids = session if session is not None else current
    await _stop_voice_chat_best_effort(rtc_ids)
    try:
        updated = await storage.session_update(
            user_id,
            session_id,
            {"status": "abandoned", "ended_at": utc_now()},
            expected_version=current.get("version"),
        )
    except StorageVersionConflictError:
        return await storage.session_get(user_id, session_id)
    return updated or current


async def reclaim_running_sessions(user_id: str) -> None:
    """同一用户只保留一场 running：先回收旧场再允许创建。"""
    for session in await storage.session_list_running(user_id):
        await abandon_session(user_id, session["id"], session)


async def get_active_session(user_id: str) -> dict:
    """单用户 MVP：取最近 running 会话；无则自动创建（Ruling R2）。

    新会话岗位取自最近简历的 position_target（解析失败回落默认）。
    """
    running = await storage.session_list_running(user_id)
    if running:
        return running[0]
    resume = await storage.resume_latest(user_id)
    position = DEFAULT_POSITION
    if resume and resume.get("structured_json"):
        try:
            position = (
                json.loads(resume["structured_json"]).get("position_target")
                or DEFAULT_POSITION
            )
        except (TypeError, json.JSONDecodeError):
            pass
    return await storage.session_create(user_id, {
        "id": str(uuid.uuid4()),
        "resume_id": resume["id"] if resume else None,
        "position": position,
        "stage": "intro",
        "status": "running",
        "started_at": utc_now(),
        "ended_at": None,
    })


def sse_chunk(text: str) -> str:
    """把增量文本包装为 RTC 要求的 OpenAI 兼容 chunk 的 SSE data 行。"""
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": settings.ARK_ENDPOINT_ID,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _restore_state(session: dict) -> dict:
    """由 checkpointer 恢复图状态；全新会话写入初始状态。"""
    config = session_config(session["id"])
    graph = get_graph()
    state = await graph.aget_state(config)
    if not state.values:
        resume = (
            await storage.resume_get(session["user_id"], session["resume_id"])
            if session["resume_id"] else None
        )
        structured = {}
        if resume and resume.get("structured_json"):
            try:
                structured = json.loads(resume["structured_json"])
            except json.JSONDecodeError:
                structured = {}
        init = {
            "session_id": session["id"],
            "position": session["position"],
            "resume": structured,
            "stage": "intro",
            "round_no": 0,
            "stage_questions": [],
            "questions_asked": [],
            "current_question": None,
            "messages": [],
            "scores": [],
            "rag_context": "",
            "pending_user_text": "",
            "report": None,
            "prompt_versions": registry.snapshot_versions(),  # P7 §0.5：会话创建即固化提示词版本
            "pending_ask": False,
        }
        await graph.aupdate_state(config, init)
        state = await graph.aget_state(config)
    return dict(state.values or {})


# ---------- 冷路径（Task 13） ----------
class ColdPathStateError(Exception):
    """不暴露持久化实现细节的稳定冷路径状态错误。"""


async def enqueue_cold_path(
    db_session: AsyncSession,
    *,
    owner_id: uuid.UUID | str,
    session_id: uuid.UUID | str,
    trigger_id: int,
) -> JobRecord:
    """在调用者事务中原子写入冷路径 Job 与 Outbox。"""
    if type(trigger_id) is not int or trigger_id <= 0:
        raise ColdPathStateError("INVALID_COLD_PATH_TRIGGER")
    try:
        owner_uuid = uuid.UUID(str(owner_id))
        session_uuid = uuid.UUID(str(session_id))
    except (TypeError, ValueError, AttributeError):
        raise ColdPathStateError("INTERVIEW_SESSION_NOT_FOUND") from None

    interview = await PostgresRepository(db_session).session_get(
        str(owner_uuid), str(session_uuid)
    )
    if not interview or interview.get("status") != "running":
        raise ColdPathStateError("INTERVIEW_SESSION_NOT_FOUND")
    return await JobDispatcher(db_session).enqueue(
        job_type=JobType.INTERVIEW_FINISH,
        owner_id=owner_uuid,
        payload_ref={
            "schema_version": 1,
            "session_id": str(session_uuid),
            "step": "round",
        },
        idempotency_key=f"interview:{session_uuid}:round:{trigger_id}",
    )


async def _latest_cold_job(session_id: str) -> JobRecord | None:
    try:
        session_uuid = uuid.UUID(str(session_id))
    except (TypeError, ValueError, AttributeError):
        raise ColdPathStateError("INTERVIEW_SESSION_NOT_FOUND") from None
    async with session_scope() as db_session:
        return await JobRepository(db_session).latest_for_session(session_uuid)


async def await_pending_cold(
    session_id: str,
    *,
    timeout: float | None = HOT_PATH_COLD_WAIT_SECONDS,
    poll_interval: float = 0.5,
) -> None:
    """在接收下一轮前短暂等待该会话最新的持久冷任务；默认 5s，超时抛 PENDING。"""
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    while True:
        job = await _latest_cold_job(session_id)
        if job is None or job.status is JobStatus.SUCCEEDED:
            return
        if job.status in {JobStatus.FAILED, JobStatus.CANCELLED}:
            raise ColdPathStateError("PREVIOUS_COLD_PATH_FAILED")
        if deadline is not None and time.monotonic() >= deadline:
            raise ColdPathStateError("PREVIOUS_COLD_PATH_PENDING")
        await asyncio.sleep(poll_interval)


async def run_cold_path(job: JobRecord) -> dict:
    """按 checkpoint 逐步执行评分、出题和报告，每步重新读取持久状态。"""
    session_id = str(job.payload_ref.get("session_id", ""))
    config = session_config(session_id)
    graph = get_graph()
    state = {}
    session = None
    for _ in range(3):
        session = await storage.session_get_internal(session_id)
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values or {})
        if (
            not session
            or str(session["user_id"]) != str(job.owner_id)
            or state.get("session_id") != session_id
        ):
            raise ColdPathStateError("INTERVIEW_SESSION_NOT_FOUND")
        if session.get("status") == "abandoned":
            return {"schema_version": 1, "session_id": session_id}
        if not snapshot.next:
            if state.get("stage") != session.get("stage"):
                session = await storage.session_update(
                    session["user_id"],
                    session_id,
                    {"stage": state.get("stage")},
                    expected_version=session["version"],
                )
                if not session:
                    raise ColdPathStateError("INTERVIEW_SESSION_NOT_FOUND")
            break
        node = snapshot.next[0]
        if node not in {"evaluator", "planner", "reporter"}:
            raise ColdPathStateError("INVALID_COLD_PATH_CHECKPOINT")
        logger.info(
            "Interview cold-path step started",
            extra={
                "event": "interview_cold_step_started",
                "job_id": str(job.id),
                "session_id": session_id,
                "step": node,
            },
        )
        await graph.ainvoke(None, config)
        state = dict((await graph.aget_state(config)).values or {})
        session = await storage.session_update(
            session["user_id"],
            session_id,
            {"stage": state.get("stage", session["stage"])},
            expected_version=session["version"],
        )
        if not session:
            raise ColdPathStateError("INTERVIEW_SESSION_NOT_FOUND")

    report = state.get("report")
    if report and session.get("status") != "abandoned":
        from services.report_service import save_report

        report_id = await save_report(session, report, state)
        await storage.session_update(session["user_id"], session_id, {
            "status": "finished", "stage": "finish", "ended_at": utc_now(),
        }, expected_version=session["version"])
        return {
            "schema_version": 1,
            "session_id": session_id,
            "report_id": report_id,
        }
    return {"schema_version": 1, "session_id": session_id}


async def schedule_cold_path(
    session_id: str,
    owner_id: str,
    trigger_id: int,
) -> JobRecord:
    """提交持久化冷路径 Job/Outbox 后返回。"""
    async with session_scope() as db_session:
        return await enqueue_cold_path(
            db_session,
            owner_id=owner_id,
            session_id=session_id,
            trigger_id=trigger_id,
        )


async def enqueue_finish_job(
    db_session: AsyncSession,
    *,
    owner_id: uuid.UUID | str,
    session_id: uuid.UUID | str,
) -> JobRecord:
    """Atomically create the one durable finish Job and Outbox per session."""
    try:
        owner_uuid = uuid.UUID(str(owner_id))
        session_uuid = uuid.UUID(str(session_id))
    except (TypeError, ValueError, AttributeError):
        raise ColdPathStateError("INTERVIEW_SESSION_NOT_FOUND") from None
    interview = await PostgresRepository(db_session).session_get(
        str(owner_uuid), str(session_uuid)
    )
    if interview is None:
        raise ColdPathStateError("INTERVIEW_SESSION_NOT_FOUND")
    return await JobDispatcher(db_session).enqueue(
        job_type=JobType.INTERVIEW_FINISH,
        owner_id=owner_uuid,
        payload_ref={
            "schema_version": 1,
            "session_id": str(session_uuid),
            "step": "finish",
        },
        idempotency_key=f"interview:{session_uuid}:finish",
    )


async def schedule_finish_job(
    session_id: str,
    owner_id: str,
) -> JobRecord:
    async with session_scope() as db_session:
        return await enqueue_finish_job(
            db_session, owner_id=owner_id, session_id=session_id
        )
