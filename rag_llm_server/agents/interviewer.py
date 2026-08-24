"""Interviewer Agent（热路径，agent-designs §1）。

模板渲染 + 消息窗口裁剪 + 流式生成；输出进 TTS 的纯文本口语短句。
每轮回调仅此一次流式 LLM 调用（热路径铁律）。
"""
import logging
from typing import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .prompts.registry import registry

logger = logging.getLogger(__name__)

WINDOW_SIZE = 10  # 最近 10 条 user/assistant 消息（agent-designs §1.4）

# 阶段指令块（agent-designs §1.6 表，随阶段注入 system 消息）
STAGE_INSTRUCTIONS = {
    "intro": "当前阶段：开场。系统欢迎语已经请候选人做自我介绍，本轮用户发言就是候选人的自我介绍。请简短承接，并立即从简历技能中选择一个与岗位相关的技术基础考点提问；不要再次要求候选人自我介绍。",
    "deepdive": "当前阶段：项目深挖。围绕候选人的项目经历提问，每个项目从背景、难点、技术取舍、数据成果四个角度展开；单点连续追问至少 2 轮再换点；同一项目深挖充分后转向「如果让你重新设计/扩展」场景设计题。",
    "technical": "当前阶段：技术基础。按系统给出的、源自候选人简历技能的题目提问，从基础原理到实际应用逐步加深。候选人答完一道后等待系统出下一题。",
    "qa": "当前阶段：候选人反问。你以真实面试官身份回答候选人的问题，简洁专业。",
    "finish": "当前阶段：收尾。感谢候选人参加面试，告知面试结果会以报告形式呈现。",
}

# 错误话术（agent-designs §1.7：LLM 失败绝不静默返回空）
ERROR_REPLY = "抱歉，我这边出了点问题，我们换个问题试试"


def _resume_brief(resume: dict) -> str:
    """简历要点串（人设模板变量 resume_brief）——含项目细节（难点/成果），供深挖锚定（P7）。"""
    if not resume:
        return "候选人尚未上传简历"
    parts = []
    skills = [s.get("name", "") for s in resume.get("skills", []) if s.get("name")]
    if skills:
        parts.append("技能：" + "、".join(skills))
    for p in (resume.get("projects", []) or [])[:3]:  # 最多 3 个项目，防超 token
        name = p.get("name", "")
        if not name:
            continue
        line = f"项目「{name}」"
        tech = "、".join(p.get("tech_stack", []) or [])[:60]
        if tech:
            line += f"｜技术栈：{tech}"
        for label, key in (("职责", "responsibilities"), ("难点", "challenges"), ("成果", "results")):
            detail = (p.get(key) or "")[:60]
            if detail:
                line += f"｜{label}：{detail}"
        parts.append(line)
    if not parts:
        return "候选人已上传简历但未提取到技能与项目信息"
    return "\n".join(parts)


def build_system_messages(state: dict) -> list[SystemMessage]:
    """拼装系统消息：人设模板 + 简历要点 + 阶段指令 + 当前题目（agent-designs §1.2）。"""
    question = state.get("current_question") or {}
    question_text = question.get("question_text") or "（暂无，按当前阶段自由主持）"
    hints = question.get("follow_up_hints") or []
    if hints:
        # P7：承接 planner 的追问提示（前 2 条），支撑单点深挖
        question_text += "\n追问提示：" + "；".join(hints[:2])
    template = registry.get_pinned("interviewer", "system", state)
    content = template.render(
        position=state.get("position", "Java后端"),
        resume_brief=_resume_brief(state.get("resume") or {}),
        stage_instruction=STAGE_INSTRUCTIONS.get(
            state.get("stage", "intro"), STAGE_INSTRUCTIONS["intro"]
        ),
        current_question=question_text,
    )
    return [SystemMessage(content=content)]


def trim_window(messages: list) -> list:
    """消息窗口裁剪：只保留最近 WINDOW_SIZE 条（§1.4）；系统前缀由调用方另传。"""
    return messages[-WINDOW_SIZE:]


async def generate_stream(state: dict, user_text: str, llm) -> AsyncIterator[str]:
    """热路径流式生成：系统消息 + 裁剪后历史 + 本轮用户输入。

    异常处理：捕获后产出错误话术 chunk（绝不静默空返回，§1.7）。
    """
    try:
        history = [{"role": m["role"], "content": m["content"]} for m in state.get("messages", [])]
        history.append({"role": "user", "content": user_text})
        msgs = build_system_messages(state)
        msgs += [
            HumanMessage(content=m["content"]) if m["role"] == "user" else AIMessage(content=m["content"])
            for m in trim_window(history)
        ]
        yielded = False
        async for chunk in llm.astream(msgs):
            if chunk.content:
                yielded = True
                yield chunk.content
        if not yielded:
            yield ERROR_REPLY
    except Exception as exc:
        logger.error("Interviewer 流式生成失败 error_type=%s", type(exc).__name__)
        yield ERROR_REPLY
