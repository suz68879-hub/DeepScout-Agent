"""录音分析 Agent（spec §12.7）：角色判定 + 候选人段四维评分报告。

说话人分离只给编号不识别身份（API 笔记）——「谁是候选人」由 LLM 判定；
报告模型与 reporter.py 同构（Report/RoundDetail），save_report 链路零改动。
"""
import json
import logging

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from .prompts.registry import registry, render_structured
from .reporter import DIMENSIONS, Report

logger = logging.getLogger("recording")

# 转写全文截断：角色判定与报告各自输入上限（字符数）
ROLE_JUDGE_TRUNCATE = 12000
REPORT_TRUNCATE = 24000


class SpeakerAssignment(BaseModel):
    candidate_speaker: str
    confidence: str = ""  # 高 / 中 / 低
    reason: str = ""


def _transcript_brief(segments: list[dict], limit: int) -> str:
    """分句列表 → 带说话人编号的文本简报（超限截断）。"""
    lines = [f"[{s['speaker']}] {s['text']}" for s in segments]
    return "\n".join(lines)[:limit]


def _fallback_assignment(transcript: list[dict]) -> SpeakerAssignment:
    """LLM 失败启发式兜底：说话量（字数）最多者为候选人（面试官提问短、候选人回答长）。"""
    totals: dict[str, int] = {}
    for s in transcript:
        totals[s["speaker"]] = totals.get(s["speaker"], 0) + len(s["text"])
    speaker = max(totals, key=totals.get) if totals else "0"
    return SpeakerAssignment(
        candidate_speaker=speaker, confidence="低",
        reason="LLM 判定失败，按说话量启发式判定（说话量最多者为候选人）",
    )


async def judge_roles(transcript: list[dict], llm=None) -> SpeakerAssignment:
    """LLM 判定候选人编号；失败回落启发式（判定存疑时报告注明，spec §12.7）。"""
    if llm is None:
        from services.agent_llm import get_agent_llm

        llm = get_agent_llm("recording_analyzer")
    template = registry.get("recording_analyzer", "role_judge")
    content = render_structured(template, SpeakerAssignment, {
        "transcript_brief": _transcript_brief(transcript, ROLE_JUDGE_TRUNCATE),
    })
    structured = llm.with_structured_output(SpeakerAssignment)
    try:
        assignment = await structured.ainvoke([HumanMessage(content=content)])
    except Exception:
        logger.warning("角色判定 LLM 失败，回落启发式", exc_info=True)
        return _fallback_assignment(transcript)
    speakers = {str(s.get("speaker")) for s in transcript}
    if assignment.candidate_speaker not in speakers:
        logger.warning(
            "角色判定 speaker 不在转写中，回落启发式 candidate_speaker=%s",
            assignment.candidate_speaker,
        )
        return _fallback_assignment(transcript)
    return assignment


def _validate(result: Report) -> bool:
    """结构校验：summary 非空 + strengths/improvements ≥ 2 + suggestions ≥ 1 + 四维评分齐全 + 总分 > 0。

    录音路径直接消费 LLM 评分（无会话路径的代码均值覆盖），
    缺维度/总分缺失视为无效输出，重试后仍失败由 worker 置 failed（终审 I-1）。
    """
    return (
        bool(result.summary.strip())
        and len(result.strengths) >= 2
        and len(result.improvements) >= 2
        and len(result.suggestions) >= 1
        and len(result.dimension_scores) == len(DIMENSIONS)
        and all(d in result.dimension_scores for d in DIMENSIONS)
        and result.overall_score > 0
    )


async def generate_recording_report(candidate_segments: list[dict], position: str, llm=None) -> Report:
    """候选人段 → 四维评分报告（LLM 直接评分）；校验失败重试 1 次，仍失败抛出由 worker 置 failed。"""
    if llm is None:
        from services.agent_llm import get_agent_llm

        llm = get_agent_llm("recording_analyzer")
    template = registry.get("recording_analyzer", "system")
    content = render_structured(template, Report, {
        "position": position,
        "transcript_brief": _transcript_brief(candidate_segments, REPORT_TRUNCATE),
    })
    structured = llm.with_structured_output(Report)
    for attempt in range(2):
        try:
            candidate = await structured.ainvoke([HumanMessage(content=content)])
            if _validate(candidate):
                return candidate
            if attempt == 0:
                content += (
                    "\n\n上次输出校验失败：strengths 至少 2 条、improvements 至少 2 条、"
                    "suggestions 至少 1 条，且四维评分（技术深度/项目理解/表达沟通/临场表现）"
                    "齐全、overall_score 大于 0。请重新输出。"
                )
        except Exception as e:
            if attempt == 0:
                content += f"\n\n上次输出校验失败：{e}。请重新输出合法 JSON。"
    raise RuntimeError("录音报告生成失败：LLM 两次输出均未通过校验")


def label_roles(transcript: list[dict], assignment: SpeakerAssignment) -> list[dict]:
    """给每段打角色标签（候选人/面试官），用于前端转写折叠区展示。"""
    return [
        {**s, "role": "候选人" if s["speaker"] == assignment.candidate_speaker else "面试官"}
        for s in transcript
    ]


def candidate_segments(transcript: list[dict], assignment: SpeakerAssignment) -> list[dict]:
    """过滤出候选人段（报告只评价候选人，spec §12.7）。"""
    return [s for s in label_roles(transcript, assignment) if s["role"] == "候选人"]
