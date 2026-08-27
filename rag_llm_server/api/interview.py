"""面试会话 API（spec §5.3）：start 创建会话并初始化图状态；finish 结束并出报告。"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agents.graph import get_graph
from agents.prompts.registry import registry
from agents.reporter import generate_report
from api.auth import get_current_user
from config import settings
from middleware.idempotency import execute_idempotent
from services.agent_llm import get_agent_llm
from services.interview_service import schedule_finish_job, session_config
from services.report_service import save_report
from services.storage import storage
from services.clock import utc_now

router = APIRouter(prefix="/api/interview", tags=["interview"])


class StartRequest(BaseModel):
    position: str = "Java后端"
    resume_id: str | None = None


class FinishRequest(BaseModel):
    session_id: str


def _load_structured(resume: dict | None) -> dict:
    if resume and resume.get("structured_json"):
        try:
            return json.loads(resume["structured_json"])
        except json.JSONDecodeError:
            return {}
    return {}


@router.post("/start")
async def start_interview(
    body: StartRequest,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """创建新会话并初始化图状态；返回会话 ID 与岗位。"""
    async def operation():
        resume = (
            await storage.resume_get(user["id"], body.resume_id)
            if body.resume_id
            else None
        )
        if body.resume_id and not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        session = await storage.session_create(user["id"], {
            "id": str(uuid.uuid4()),
            "resume_id": body.resume_id,
            "position": body.position,
            "stage": "intro",
            "status": "running",
            "started_at": utc_now(),
            "ended_at": None,
        })
        await get_graph().aupdate_state(session_config(session["id"]), {
            "session_id": session["id"],
            "position": body.position,
            "resume": _load_structured(resume),
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
        })
        return {"session_id": session["id"], "position": body.position, "stage": "intro"}

    return await execute_idempotent(
        request, user, body.model_dump(mode="json"), operation
    )


@router.post("/finish", status_code=202)
async def finish_interview(
    body: FinishRequest,
    user: dict = Depends(get_current_user),
    request: Request = None,
):
    """Accept durable report generation and return its queryable Job ID."""
    async def operation():
        session = await storage.session_get(user["id"], body.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        if settings.ENABLE_LEGACY_SYNC_FINISH:
            if session.get("status") == "finished":
                raise HTTPException(status_code=409, detail="会话已结束，请勿重复请求")
            config = session_config(body.session_id)
            graph = get_graph()
            await graph.aupdate_state(config, {"stage": "finish"}, as_node="planner")
            state = dict((await graph.aget_state(config)).values or {})
            if state.get("session_id") != body.session_id:
                raise HTTPException(status_code=409, detail="会话状态不存在或已过期")
            report = await generate_report(state, get_agent_llm("reporter"))
            report_id = await save_report(session, report.model_dump(), state)
            await storage.session_update(user["id"], body.session_id, {
                "status": "finished", "stage": "finish", "ended_at": utc_now(),
            })
            return {
                "session_id": body.session_id,
                "report_id": report_id,
                "status": "finished",
            }

        config = session_config(body.session_id)
        graph = get_graph()
        if session.get("status") != "finished":
            await graph.aupdate_state(config, {"stage": "finish"}, as_node="planner")
            state = dict((await graph.aget_state(config)).values or {})
            if state.get("session_id") != body.session_id:
                raise HTTPException(status_code=409, detail="会话状态不存在或已过期")
        job = await schedule_finish_job(body.session_id, user["id"])
        return {
            "job_id": str(job.id),
            "session_id": body.session_id,
            "status": "pending",
        }

    return await execute_idempotent(
        request, user, body.model_dump(mode="json"), operation
    )


@router.get("/state")
async def interview_state(session_id: str, user: dict = Depends(get_current_user)):
    """只读会话状态（Plan 3 T1）：评分浮层与阶段指示器的数据源（spec §2.3 后端下发阶段）。"""
    session = await storage.session_get(user["id"], session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    state = dict((await get_graph().aget_state(session_config(session_id))).values or {})
    if state.get("session_id") != session_id:
        # checkpoint 丢失/新建空状态时不返回误导性空状态（与 /finish 守卫同语义）
        raise HTTPException(status_code=409, detail="会话状态不存在或已过期")
    return {
        "session_id": session_id,
        "stage": state.get("stage", session.get("stage") or "intro"),
        "round_no": state.get("round_no", 0),
        "current_question": state.get("current_question"),
        "scores": state.get("scores", []),
    }
