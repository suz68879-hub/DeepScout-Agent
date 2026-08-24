"""Planner Agent（冷路径出题，agent-designs §2）。

CoT 提示词 + few-shot 示例 + 结构化输出；解析/去重失败重试 1 次，
仍失败落兜底题库（绝不产出 None 题目）。
"""
import json

import yaml
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from .prompts.registry import registry, render_structured

# 兜底题库（agent-designs §2.7）：LLM 失败时按阶段顺序取未出过的题
FALLBACK_QUESTIONS = {
    "deepdive": [
        {
            "question_text": "能详细讲讲你在这个项目里遇到的最大技术难点，以及你是怎么解决的吗？",
            "stage": "deepdive", "topic": "项目难点", "difficulty": 1,
            "reference_points": [], "follow_up_hints": [],
            "reason": "兜底题库（LLM 出题失败）",
        },
        {
            "question_text": "这个项目如果重新做一次，你会改变哪些技术选型？",
            "stage": "deepdive", "topic": "技术取舍", "difficulty": 2,
            "reference_points": [], "follow_up_hints": [],
            "reason": "兜底题库（LLM 出题失败）",
        },
        {
            "question_text": "项目上线后有没有用数据验证过效果？具体有哪些指标？",
            "stage": "deepdive", "topic": "数据成果", "difficulty": 2,
            "reference_points": [], "follow_up_hints": [],
            "reason": "兜底题库（LLM 出题失败）",
        },
    ],
    "technical": [
        {
            "question_text": "谈谈你对 Java 内存模型的理解。",
            "stage": "technical", "topic": "JVM", "difficulty": 1,
            "reference_points": [], "follow_up_hints": [],
            "reason": "兜底题库（LLM 出题失败）",
        },
        {
            "question_text": "MySQL 索引失效有哪些常见场景？",
            "stage": "technical", "topic": "数据库", "difficulty": 1,
            "reference_points": [], "follow_up_hints": [],
            "reason": "兜底题库（LLM 出题失败）",
        },
        {
            "question_text": "谈谈你对 RAG 检索增强生成的理解。",
            "stage": "technical", "topic": "AI Agent", "difficulty": 2,
            "reference_points": [], "follow_up_hints": [],
            "reason": "兜底题库（LLM 出题失败）",
        },
    ],
}


class Question(BaseModel):
    question_text: str
    stage: str
    topic: str
    difficulty: int
    reference_points: list[str]
    follow_up_hints: list[str]
    reason: str


def _resume_anchor(stage: str, resume: dict) -> str:
    projects = resume.get("projects", []) or []
    skills = resume.get("skills", []) or []
    if stage == "deepdive" and projects:
        return projects[0].get("name", "")
    if stage == "technical" and skills:
        return skills[0].get("name", "")
    if stage == "technical" and projects:
        tech_stack = projects[0].get("tech_stack", []) or []
        return tech_stack[0] if tech_stack else projects[0].get("name", "")
    return ""


def _fallback_question(stage: str, asked: list[str], resume: dict | None = None) -> Question:
    """从兜底题库取未出过的题，并尽可能显式锚定简历。"""
    pool = FALLBACK_QUESTIONS.get(stage, FALLBACK_QUESTIONS["technical"])
    anchor = _resume_anchor(stage, resume or {})
    technical_prompts = (
        "请讲讲它的核心原理和关键机制。",
        "它适合解决哪些问题，常见的使用误区有哪些？",
        "在实际项目中你为什么选择它，有没有比较过替代方案？",
    )
    for index, q in enumerate(pool):
        if anchor and stage == "technical":
            question_text = f"你简历里提到 {anchor}，{technical_prompts[index]}"
        else:
            question_text = f"你简历里提到 {anchor}，{q['question_text']}" if anchor else q["question_text"]
        if question_text not in asked:
            return Question(**{
                **q,
                "question_text": question_text,
                "topic": anchor if anchor and stage == "technical" else q["topic"],
            })
    q = pool[0]
    if anchor and stage == "technical":
        question_text = f"你简历里提到 {anchor}，{technical_prompts[0]}"
    else:
        question_text = f"你简历里提到 {anchor}，{q['question_text']}" if anchor else q["question_text"]
    return Question(**{
        **q,
        "question_text": question_text,
        "topic": anchor if anchor and stage == "technical" else q["topic"],
    })


async def generate_question(state: dict, rag_context: str = "", llm=None) -> Question:
    """出题：结构化简历 + 岗位 + 阶段 + 已出题目 + RAG 参考 → 下一题。

    失败策略：解析/去重失败重试 1 次（附错误反馈）→ 兜底题库。
    """
    if llm is None:
        from services.agent_llm import get_agent_llm

        llm = get_agent_llm("planner")
    template = registry.get_pinned("planner", "system", state)
    examples = yaml.dump(registry.get_examples_pinned("planner", "examples", state), allow_unicode=True)
    rag = rag_context or "本题无参考要点，按通用标准出题"
    content = render_structured(template, Question, {
        "position": state.get("position", "Java后端"),
        "stage": state.get("stage", "technical"),
        "resume_json": json.dumps(state.get("resume") or {}, ensure_ascii=False),
        "asked_questions": json.dumps(
            [q.get("question_text") for q in state.get("questions_asked", [])],
            ensure_ascii=False,
        ),
        "rag_context": rag,
        "examples": examples,  # Ruling R4：few-shot 随模板变量注入（v2 自带示例段）
    })
    asked = [q.get("question_text") for q in state.get("questions_asked", [])]
    structured = llm.with_structured_output(Question)
    for attempt in range(2):
        try:
            result = await structured.ainvoke([HumanMessage(content=content)])
            if result.question_text in asked:
                raise ValueError("题目与已出题目重复")
            return result
        except Exception as e:
            if attempt == 0:
                content += f"\n\n上次输出校验失败：{e}。请重新输出合法 JSON。"
            else:
                return _fallback_question(
                    state.get("stage", "technical"), asked, state.get("resume") or {}
                )
    return _fallback_question(  # 理论不可达，防御
        state.get("stage", "technical"), asked, state.get("resume") or {}
    )
