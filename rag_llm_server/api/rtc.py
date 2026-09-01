"""Authenticated browser RTC routes and signed provider callback."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agents.graph import get_graph
from agents.interviewer import ERROR_REPLY, generate_stream
from api.auth import get_current_user, get_rate_limiter
from config import settings
from services.agent_llm import get_agent_llm
from services.callback_verify import callback_replay_id, verify_callback
from services.callback_replay import claim_callback_replay
from services.interview_service import (
    HOT_PATH_COLD_WAIT_SECONDS,
    ColdPathStateError,
    _restore_state,
    await_pending_cold,
    schedule_cold_path,
    session_config,
    readiness_gate_reply,
    sse_chunk,
)
from services.rtc_service import (
    ALLOWED_VOICE_CHAT_ACTIONS,
    RTC_LOCK_WAIT_SECONDS,
    RTCConfigurationError,
    call_voice_chat_openapi,
    get_rtc_lock,
    get_scenes_payload,
)
from services.distributed_lock import LockBusy, LockLost
from services.redis_client import SharedStateUnavailable
from services.storage import storage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["rtc"])


class SceneRequest(BaseModel):
    SessionId: str


class ProxyRequest(BaseModel):
    SessionId: str
    SceneID: str | None = None


@router.post("/getScenes")
async def get_scenes(body: SceneRequest, user: dict = Depends(get_current_user)):
    session = await storage.session_get(user["id"], body.SessionId)
    if not session:
        raise HTTPException(status_code=404, detail="interview session not found")
    return get_scenes_payload(session)


@router.post("/proxy")
async def proxy(
    request: Request,
    body: ProxyRequest,
    user: dict = Depends(get_current_user),
):
    session = await storage.session_get(user["id"], body.SessionId)
    if not session:
        raise HTTPException(status_code=404, detail="interview session not found")
    action = request.query_params.get("Action")
    if action not in ALLOWED_VOICE_CHAT_ACTIONS:
        raise HTTPException(status_code=400, detail="unsupported RTC action")
    version = request.query_params.get("Version", "2024-12-01")
    logger.info("Forwarding RTC OpenAPI action=%s session=%s", action, session["id"])
    try:
        return await call_voice_chat_openapi(
            action, version, session, body.model_dump(exclude_none=True),
        )
    except RTCConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LockBusy:
        raise HTTPException(
            status_code=409,
            detail="RTC session is busy; retry the request",
            headers={"Retry-After": "2"},
        ) from None
    except LockLost:
        raise HTTPException(
            status_code=409,
            detail="RTC session lock was lost; retry the request",
            headers={"Retry-After": "1"},
        ) from None
    except SharedStateUnavailable:
        raise HTTPException(status_code=503, detail="shared state unavailable") from None
    except LookupError:
        raise HTTPException(status_code=404, detail="interview session not found") from None


@router.post("/api/chat_callback")
async def chat_callback(request: Request, rtc_callback_id: str | None = None):
    if not rtc_callback_id:
        raise HTTPException(status_code=400, detail="rtc_callback_id is required")
    try:
        body = await request.json()
    except Exception:
        logger.warning("RTC callback contained invalid JSON")
        return {"text": ERROR_REPLY}

    if not isinstance(body, dict):
        logger.warning("RTC callback body was not an object")
        return JSONResponse({"text": "signature verification failed"}, status_code=403)
    if not settings.RTC_CALLBACK_SECRET:
        raise HTTPException(status_code=503, detail="RTC callback verification is not configured")
    if not verify_callback(body, settings.RTC_CALLBACK_SECRET):
        logger.warning("RTC callback signature verification failed")
        return JSONResponse({"text": "signature verification failed"}, status_code=403)
    try:
        decision = await get_rate_limiter().consume_callback(
            request.client.host if getattr(request, "client", None) else "unknown",
            rtc_callback_id,
        )
    except SharedStateUnavailable:
        raise HTTPException(status_code=503, detail="shared state unavailable") from None
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="too many requests",
            headers={"Retry-After": str(decision.retry_after)},
        )
    try:
        claimed = await claim_callback_replay(callback_replay_id(body))
    except SharedStateUnavailable:
        raise HTTPException(status_code=503, detail="shared state unavailable") from None
    if not claimed:
        logger.warning("RTC callback replay rejected")
        return JSONResponse({"text": "signature verification failed"}, status_code=403)

    session = await storage.session_get_by_callback(rtc_callback_id)
    if not session or session.get("status") != "running":
        raise HTTPException(status_code=404, detail="interview session not found")
    messages = body.get("messages", [])
    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(messages[-1], dict)
        or messages[-1].get("role") != "user"
    ):
        logger.info("Ignoring RTC callback without a final user message")
        return {"text": ""}

    async def generate_sse():
        spoken_done = False
        try:
            async with get_rtc_lock().lease_wait(
                session["id"], timeout=RTC_LOCK_WAIT_SECONDS,
            ):
                try:
                    await await_pending_cold(
                        session["id"], timeout=HOT_PATH_COLD_WAIT_SECONDS,
                    )
                    user_text = messages[-1].get("content", "")
                    if not isinstance(user_text, str):
                        user_text = str(user_text)
                    config = session_config(session["id"])
                    state = await _restore_state(session)
                    graph = get_graph()
                    gate_reply = readiness_gate_reply(state, user_text)
                    if gate_reply is not None:
                        await graph.aupdate_state(config, {
                            "messages": [
                                {"role": "user", "content": user_text},
                                {"role": "assistant", "content": gate_reply},
                            ],
                            "pending_user_text": "",
                        }, as_node="interviewer")
                        yield sse_chunk(gate_reply)
                    else:
                        await graph.aupdate_state(config, {
                            "messages": [{"role": "user", "content": user_text}],
                            "pending_user_text": user_text,
                        }, as_node="__start__")
                        await graph.ainvoke(None, config)
                        full = ""
                        async for chunk in generate_stream(
                            state, user_text, get_agent_llm("interviewer"),
                        ):
                            full += chunk
                            yield sse_chunk(chunk)
                        await graph.aupdate_state(config, {
                            "messages": [{"role": "assistant", "content": full}],
                            "pending_user_text": "",
                        }, as_node="interviewer")
                        latest_state = dict((await graph.aget_state(config)).values or {})
                        await schedule_cold_path(
                            session["id"],
                            session["user_id"],
                            len(latest_state.get("messages", [])),
                        )
                    yield "data: [DONE]\n\n"
                    spoken_done = True
                except (ColdPathStateError, LockBusy, LockLost, SharedStateUnavailable):
                    logger.warning(
                        "RTC callback hot path degraded",
                        extra={
                            "event": "interview_hot_path_degraded",
                            "session_id": session["id"],
                        },
                    )
                    yield sse_chunk(ERROR_REPLY)
                    yield "data: [DONE]\n\n"
                    spoken_done = True
                except Exception:
                    logger.exception(
                        "RTC callback hot path failed",
                        extra={
                            "event": "interview_hot_path_failed",
                            "session_id": session["id"],
                        },
                    )
                    yield sse_chunk(ERROR_REPLY)
                    yield "data: [DONE]\n\n"
                    spoken_done = True
        except (LockBusy, LockLost, SharedStateUnavailable):
            if spoken_done:
                logger.warning(
                    "RTC callback lock release failed after stream completed",
                    extra={
                        "event": "interview_hot_path_lock_release_failed",
                        "session_id": session["id"],
                    },
                )
                return
            logger.warning(
                "RTC callback hot path degraded",
                extra={
                    "event": "interview_hot_path_degraded",
                    "session_id": session["id"],
                },
            )
            yield sse_chunk(ERROR_REPLY)
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
