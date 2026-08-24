"""Authenticated natural-language analytics over tenant-scoped SQLite views."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agents.text2sql import generate_query
from api.auth import get_current_user
from mcp.sqlite_server import query
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
        rows = query(sql_query.sql, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="SQL validation failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="query execution failed") from exc
    return {
        "sql": sql_query.sql,
        "explanation": sql_query.explanation,
        "chart_type": sql_query.chart_type,
        "rows": rows,
    }
