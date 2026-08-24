"""Agent LLM 客户端工厂（agent-designs §0.4 参数表落地）。

方舟 OpenAI 兼容端点，LangChain ChatOpenAI 统一接入；
每 Agent 独立端点可配置（config.AGENT_ENDPOINT_KEYS），参数按表映射。
"""
from langchain_openai import ChatOpenAI

from config import ARK_BASE_URL, settings

# agent-designs §0.4：temperature / top_p / max_tokens / timeout（stream 在调用层控制）
# max_tokens / timeout 已按推理模型上调（原 512-2048 / 10-60s）：
# 端点绑定思考类模型时 reasoning 会消耗大半额度且思考耗时较长，原值实测
# 触发 LengthFinishReasonError（输出为空）与 APITimeoutError
AGENT_PARAMS: dict[str, dict] = {
    "interviewer":   {"temperature": 0.7, "top_p": 0.9, "max_tokens": 4096, "timeout": 120},
    "planner":       {"temperature": 0.3, "top_p": 0.8, "max_tokens": 4096, "timeout": 120},
    "evaluator":     {"temperature": 0.0, "top_p": 1.0, "max_tokens": 4096, "timeout": 120},
    "reporter":      {"temperature": 0.2, "top_p": 0.8, "max_tokens": 4096, "timeout": 180},
    "resume_parser": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 4096, "timeout": 120},
    "text2sql":      {"temperature": 0.1, "top_p": 0.8, "max_tokens": 4096, "timeout": 120},
    "recording_analyzer": {"temperature": 0.2, "top_p": 0.8, "max_tokens": 4096, "timeout": 180},
}


def get_agent_llm(agent: str) -> ChatOpenAI:
    """按 Agent 名构造 ChatOpenAI 客户端；未知 Agent 名抛 ValueError（调用方错误）。"""
    if agent not in AGENT_PARAMS:
        raise ValueError(f"未知 Agent: {agent}（可选：{list(AGENT_PARAMS)}）")
    endpoint = settings.agent_endpoint_id(agent)
    if not endpoint:
        raise ValueError(f"Agent {agent} 端点未配置（{settings.AGENT_ENDPOINT_KEYS[agent]} 与 ARK_ENDPOINT_ID 均为空）")
    p = AGENT_PARAMS[agent]
    return ChatOpenAI(
        base_url=ARK_BASE_URL,
        api_key=settings.ARK_API_KEY,
        model=endpoint,
        temperature=p["temperature"],
        max_tokens=p["max_tokens"],
        timeout=p["timeout"],
        model_kwargs={"top_p": p["top_p"]},
        # 端点绑定思考类模型时关闭思考：实测思考不收敛会吃满 max_tokens
        # （LengthFinishReasonError），disabled 后结构化输出正常。
        # 若端点换为非思考模型且此参数报错，删除本行即可。
        extra_body={"thinking": {"type": "disabled"}},
    )


def get_vision_llm() -> ChatOpenAI:
    """视觉模型（简历扫描件 OCR，agent-designs §5.3）。

    仅支持图片输入；需要 ARK_VISION_ENDPOINT_ID，不设回落（配置缺失应显式失败）。
    """
    if not settings.ARK_VISION_ENDPOINT_ID:
        raise ValueError("未配置 ARK_VISION_ENDPOINT_ID，无法调用视觉模型")
    return ChatOpenAI(
        base_url=ARK_BASE_URL,
        api_key=settings.ARK_API_KEY,
        model=settings.ARK_VISION_ENDPOINT_ID,
        temperature=0.1,
        max_tokens=2048,
        timeout=30,
    )
