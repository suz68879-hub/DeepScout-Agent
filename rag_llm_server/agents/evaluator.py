"""Evaluator Agent（冷路径评分，agent-designs §3）。

Rubric + 锚点 + CoT（先理由后分数）；overall_score 由代码按维度均值覆盖，
LLM 输出的总分字段不采信（防止总分与分维度不一致，§4.2 同原则）。
"""
import json

import yaml
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from .prompts.registry import registry, render_structured

DIMENSIONS = ["技术深度", "项目理解", "表达沟通", "临场表现"]
MIN_SCORE, MAX_SCORE = 1, 10


class DimensionScore(BaseModel):
    score: int = Field(ge=MIN_SCORE, le=MAX_SCORE)
    reason: str


class RoundScore(BaseModel):
    dimensions: dict[str, DimensionScore]
    overall_score: float = 0.0  # 占位：代码计算均值后覆盖
    strengths: list[str] = []
    improvements: list[str] = []
    comment: str = ""


def _mean(scores: dict[str, DimensionScore]) -> float:
    return round(sum(d.score for d in scores.values()) / len(scores), 1)


def _validate(result: RoundScore) -> RoundScore | None:
    """维度齐全校验（四个维度必须齐全、分值在 1-10）；不合法返回 None。"""
    if set(result.dimensions) != set(DIMENSIONS):
        return None
    for d in DIMENSIONS:
        if not (MIN_SCORE <= result.dimensions[d].score <= MAX_SCORE):
            return None
    return result


async def evaluate_round(
    position: str,
    question: str,
    reference_points: list[str],
    answer: str,
    rag_context: str = "",
    llm=None,
    prompt_versions: dict | None = None,
) -> dict:
    """单轮评分；校验失败重试 1 次（§0.6），LLM 异常/校验仍失败返回 {"status": "failed"}（不阻断流程）。

    prompt_versions：会话固化的提示词版本快照；None 时取最新（调试/冷路径语义，§0.5）。
    """
    if llm is None:
        from services.agent_llm import get_agent_llm

        llm = get_agent_llm("evaluator")
    state = {"prompt_versions": prompt_versions} if prompt_versions else None
    rubric_data = registry.get_examples_pinned("evaluator", "rubric", state)
    anchors_data = registry.get_examples_pinned("evaluator", "anchors", state)
    template = registry.get_pinned("evaluator", "system", state)
    # R-T10-3a：§3.3 合并语义——参考要点优先，RAG 补充；均无才用通用标准
    merged = list(reference_points)
    if rag_context:
        merged.append(rag_context)
    if not merged:
        merged = ["本题无参考要点，按通用标准评分"]
    content = render_structured(template, RoundScore, {
        "position": position,
        "question": question,
        "reference_points": json.dumps(merged, ensure_ascii=False),
        "answer": answer,
        "rubric": yaml.dump(rubric_data["dimensions"], allow_unicode=True),
        "anchors": yaml.dump(anchors_data["anchors"], allow_unicode=True),
    })
    structured = llm.with_structured_output(RoundScore)
    # R-T10-3b：§0.6 重试表——仅解析/校验失败（None 或 _validate None）重试 1 次；
    # LLM 异常/超时直接 failed，不重试（§3.8 语义）
    for attempt in range(2):
        try:
            result = await structured.ainvoke([HumanMessage(content=content)])
        except Exception:
            return {"status": "failed"}
        if result is None:  # R-T10-1：部分 langchain 版本对空输出返回 None 而非抛异常
            detail = "模型返回空输出"
        else:
            validated = _validate(result)
            if validated is not None:
                validated.overall_score = _mean(validated.dimensions)  # 代码算均值，覆盖 LLM 总分
                # 成功带 status="ok"（D1：brief 测试断言 r["status"] != "failed"；消费方统一
                # 用 s.get("status") != "failed" 过滤，ok 键无害）
                return {"status": "ok", **validated.model_dump()}
            detail = f"四个维度须齐全（{DIMENSIONS}）且分数在 {MIN_SCORE}-{MAX_SCORE}"
        if attempt == 1:
            return {"status": "failed"}
        content += f"\n\n上次输出不符合评分规范，请重新输出合法 JSON：{detail}"
    return {"status": "failed"}  # 理论不可达，防御
