"""仅在 ENABLE_DEBUG_ROUTES=true 时注册的本地调试路由。"""
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.interviewer import generate_stream
from api.auth import get_current_user
from services.agent_llm import get_agent_llm
from services.rag_service import rag_service

router = APIRouter(
    prefix="/debug", tags=["debug"], dependencies=[Depends(get_current_user)],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class DebugRequest(BaseModel):
    history: list[ChatMessage] | None = None
    question: str


@router.post("/chat")
async def debug_chat(request: DebugRequest):
    async def generate_text():
        state = {
            "session_id": "debug", "position": "Java后端", "resume": None,
            "stage": "deepdive", "round_no": 0, "current_question": None,
            "messages": [message.model_dump() for message in request.history or []],
        }
        async for text in generate_stream(
            state, request.question, get_agent_llm("interviewer"),
        ):
            yield text
    return StreamingResponse(generate_text(), media_type="text/plain")


@router.get("/rag")
async def debug_rag(query: str):
    if not query:
        return {"error": "请提供 query 参数"}
    context = await rag_service.retrieve(query)
    return {
        "query": query, "retrieved_context": context,
        "length": len(context) if context else 0,
        "status": "success" if context else "no_results_or_error",
    }
