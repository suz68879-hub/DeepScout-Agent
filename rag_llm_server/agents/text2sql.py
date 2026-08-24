"""Text2SQL Agent（独立冷路径，agent-designs §6）。

NL → 只读 SQL + 解释 + 图表类型；LLM 失败或 SQL 校验失败降级到模板查询（§6.9）。
"""
import json

import yaml
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from .prompts.registry import registry, render_structured

SCHEMA_DESCRIPTION = """
resume(id, content, structured_json, source, status, created_at, updated_at)
interview_session(id, resume_id, position, stage, status, started_at, ended_at)
message(id, session_id, role, content, seq, created_at)
interview_report(id, session_id, scores_json, feedback_json, suggestions_json, md_path, created_at)
"""

CHART_TYPES = ("line", "bar", "radar", "pie", "table")

# 兜底模板查询（spec §5.8 五类，与 agent-designs §6.8 一致）
TEMPLATE_QUERIES = [
    {
        "name": "近 N 次总评分趋势",
        "keywords": ["趋势", "走势", "平均分", "总分"],
        "sql": "SELECT session_id, scores_json, created_at FROM interview_report ORDER BY created_at DESC LIMIT 10",
        "explanation": "取出最近 10 份报告的评分 JSON，由应用层解析后绘制趋势",
        "chart_type": "line",
    },
    {
        "name": "各维度雷达对比",
        "keywords": ["雷达", "维度", "对比"],
        "sql": "SELECT session_id, scores_json, created_at FROM interview_report ORDER BY created_at DESC LIMIT 10",
        "explanation": "聚合最近报告的四维度评分绘制雷达图",
        "chart_type": "radar",
    },
    {
        "name": "题目类型分布",
        "keywords": ["题目", "类型", "分布"],
        "sql": "SELECT session_id, feedback_json FROM interview_report ORDER BY created_at DESC LIMIT 10",
        "explanation": "按题面关键词归类最近报告的题目分布",
        "chart_type": "pie",
    },
    {
        "name": "高频薄弱点",
        "keywords": ["薄弱", "改进", "短板"],
        "sql": "SELECT session_id, feedback_json FROM interview_report ORDER BY created_at DESC LIMIT 10",
        "explanation": "统计最近报告中出现最多的改进点",
        "chart_type": "bar",
    },
    {
        "name": "面试频次与平均分",
        "keywords": ["频次", "次数", "多少"],
        "sql": "SELECT started_at, status FROM interview_session ORDER BY started_at DESC LIMIT 100",
        "explanation": "统计面试频次（报告侧聚合平均分）",
        "chart_type": "bar",
    },
]


class SqlQuery(BaseModel):
    sql: str
    explanation: str
    chart_type: str


def _match_template(question: str) -> dict:
    for t in TEMPLATE_QUERIES:
        if any(k in question for k in t["keywords"]):
            return t
    return TEMPLATE_QUERIES[0]


def _template_as_query(t: dict) -> SqlQuery:
    return SqlQuery(sql=t["sql"], explanation=t["explanation"], chart_type=t["chart_type"])


async def generate_query(question: str, llm=None) -> SqlQuery:
    """NL → 只读 SQL；LLM 失败或 SQL 校验失败降级到模板查询（§6.9）。"""
    if llm is None:
        from services.agent_llm import get_agent_llm

        llm = get_agent_llm("text2sql")
    template = registry.get("text2sql", "system")
    examples = yaml.dump(registry.get_examples("text2sql", "examples"), allow_unicode=True)
    content = render_structured(template, SqlQuery, {
        "schema_description": SCHEMA_DESCRIPTION,
        "examples": examples,
    })
    structured = llm.with_structured_output(SqlQuery)
    try:
        result = await structured.ainvoke(
            [HumanMessage(content=content + f"\n\n用户问题：{question}")]
        )
        # R-T14-1：部分 langchain 版本空输出返回 None 不抛异常，同样降级模板查询
        if result is None:
            return _template_as_query(_match_template(question))
    except Exception:
        return _template_as_query(_match_template(question))
    from mcp.sqlite_server import validate_query

    try:
        validate_query(result.sql)
    except ValueError:
        return _template_as_query(_match_template(question))
    if result.chart_type not in CHART_TYPES:
        result.chart_type = "table"
    return result
