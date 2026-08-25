"""Authenticated natural-language analytics over tenant-scoped database views."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agents.text2sql import generate_query
from api.auth import get_current_user
from config import settings
from mcp.postgres_server import query as postgres_query
from mcp.sqlite_server import query as sqlite_query
from services.agent_llm import get_agent_llm

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class QueryRequest(BaseModel):
    question: str


@router.post("/query")
async def analytics_query(body: QueryRequest, user: dict = Depends(get_current_user)):
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question cannot be empty")
    sql_query = await generate_query(body.question, get_agent_llm("text2sql"))
    try:
        if settings.STORAGE_BACKEND == "postgres":
            result = await postgres_query(sql_query.sql, user["id"])
            rows = result.items
            truncated = result.truncated
        else:
            rows = await asyncio.to_thread(sqlite_query, sql_query.sql, user["id"])
            truncated = False
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="SQL validation failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="query execution failed") from exc
    return {
        "sql": sql_query.sql,
        "explanation": sql_query.explanation,
        "chart_type": sql_query.chart_type,
        "rows": rows,
        "truncated": truncated,
    }
