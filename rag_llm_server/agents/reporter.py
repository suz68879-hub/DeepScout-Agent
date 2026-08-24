"""Reporter Agent（冷路径报告，agent-designs §4）。

均值与总分由代码计算（LLM 只写评语）；校验失败重试 1 次，
仍失败落兜底报告（数据自动汇总模式）。
"""
import json

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from .evaluator import DIMENSIONS
from .prompts.registry import registry, render_structured

MESSAGES_BRIEF_COUNT = 12   # 对话摘要取最近 12 条（6 轮）
CONTENT_TRUNCATE = 200      # 单条消息截断长度


class RoundDetail(BaseModel):
    round_no: int
    question: str
    answer_summary: str
    comment: str


class Report(BaseModel):
    summary: str = ""
    dimension_scores: dict[str, float] = {}
    overall_score: float = 0.0
    round_details: list[RoundDetail] = []
    strengths: list[str] = []
    improvements: list[str] = []
    suggestions: list[str] = []


def compute_average_scores(scores: list[dict]) -> tuple[dict[str, float], float]:
    """逐轮成功评分按维度平均（代码计算，LLM 不参与，agent-designs §4.2）。"""
    ok = [s for s in scores if s.get("status") != "failed"]
    if not ok:
        return {d: 0.0 for d in DIMENSIONS}, 0.0
    avg = {
        d: round(sum(s["dimensions"][d]["score"] for s in ok) / len(ok), 1)
        for d in DIMENSIONS
    }
    return avg, round(sum(avg.values()) / len(DIMENSIONS), 1)


def _validate(result: Report) -> bool:
    """结构校验：summary 非空 + strengths ≥ 2 + improvements ≥ 2 + suggestions ≥ 1。"""
    return (
        bool(result.summary.strip())
        and len(result.strengths) >= 2
        and len(result.improvements) >= 2
        and len(result.suggestions) >= 1
    )


def _fallback_report() -> Report:
    """兜底报告：数据自动汇总，无 AI 点评（agent-designs §4.4）。"""
    return Report(summary="本次报告为数据自动汇总模式（AI 点评生成失败）。")


async def generate_report(state: dict, llm=None) -> Report:
    """生成报告；维度分/总分一律由代码均值覆盖（无论 LLM 输出还是兜底）。"""
    dims, overall = compute_average_scores(state.get("scores", []))
    if llm is None:
        from services.agent_llm import get_agent_llm

        llm = get_agent_llm("reporter")
    resume = state.get("resume") or {}
    skills = [s.get("name", "") for s in resume.get("skills", []) if s.get("name")]
    projects = [p.get("name", "") for p in resume.get("projects", []) if p.get("name")]
    resume_brief = f"技能栈：{', '.join(skills) or '未知'}；项目：{', '.join(projects) or '未知'}"
    brief = [
        {"role": m["role"], "content": m["content"][:CONTENT_TRUNCATE]}
        for m in state.get("messages", [])[-MESSAGES_BRIEF_COUNT:]
    ]
    template = registry.get_pinned("reporter", "system", state)
    content = render_structured(template, Report, {
        "position": state.get("position", "Java后端"),
        "resume_brief": resume_brief,
        "scores_json": json.dumps(
            {"dimension_scores": dims, "overall_score": overall}, ensure_ascii=False
        ),
        "messages_brief": json.dumps(brief, ensure_ascii=False),
    })
    structured = llm.with_structured_output(Report)
    result = None
    for attempt in range(2):
        try:
            candidate = await structured.ainvoke([HumanMessage(content=content)])
            if _validate(candidate):
                result = candidate
                break
            if attempt == 0:
                content += "\n\n上次输出校验失败：strengths 至少 2 条、improvements 至少 2 条、suggestions 至少 1 条。请重新输出。"
        except Exception as e:
            if attempt == 0:
                content += f"\n\n上次输出校验失败：{e}。请重新输出合法 JSON。"
    if result is None:
        result = _fallback_report()
    result.dimension_scores = dims  # 代码均值，覆盖 LLM 输出
    result.overall_score = overall
    return result
