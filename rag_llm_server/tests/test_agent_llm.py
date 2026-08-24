"""Agent LLM 工厂：参数表、端点回落与视觉模型。"""
import pytest
from langchain_openai import ChatOpenAI

from config import Config
from services.agent_llm import AGENT_PARAMS, get_agent_llm, get_vision_llm


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    monkeypatch.setattr(Config, "ARK_API_KEY", "test-key")
    for key in Config.AGENT_ENDPOINT_KEYS.values():
        monkeypatch.delenv(key, raising=False)


def test_params_table_matches_design_doc():
    # agent-designs §0.4 参数表逐项固化（防漂移）
    assert AGENT_PARAMS["interviewer"]["temperature"] == 0.7
    assert AGENT_PARAMS["planner"]["temperature"] == 0.3
    assert AGENT_PARAMS["evaluator"]["temperature"] == 0.0
    assert AGENT_PARAMS["reporter"]["temperature"] == 0.2
    assert AGENT_PARAMS["resume_parser"]["temperature"] == 0.1
    assert AGENT_PARAMS["text2sql"]["temperature"] == 0.1
    assert AGENT_PARAMS["recording_analyzer"]["temperature"] == 0.2
    assert set(AGENT_PARAMS) == {
        "interviewer", "planner", "evaluator", "reporter", "resume_parser", "text2sql",
        "recording_analyzer",
    }


def test_params_reserve_room_for_reasoning_tokens():
    # 回归：ARK_ENDPOINT_ID 绑定推理（思考）模型时，max_tokens 会被 reasoning 全额消耗
    # （实测 evaluator 1024 → LengthFinishReasonError，reasoning_tokens=1024、输出 0）。
    # 各 Agent max_tokens 必须为推理预留空间，否则结构化输出必失败。
    for agent, p in AGENT_PARAMS.items():
        assert p["max_tokens"] >= 2048, f"{agent} max_tokens 未为推理模型预留空间"


def test_params_timeout_suits_reasoning_model():
    # 回归：推理模型思考时间常超 30s（实测 evaluator timeout=30 → APITimeoutError），
    # 各 Agent timeout 必须放宽到推理模型可用区间。
    for agent, p in AGENT_PARAMS.items():
        assert p["timeout"] >= 120, f"{agent} timeout 不足推理模型思考时间"


def test_get_agent_llm_builds_with_default_endpoint():
    llm = get_agent_llm("evaluator")
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "ep-default"
    assert llm.temperature == 0.0
    assert llm.max_tokens == 4096


def test_get_agent_llm_disables_thinking_on_reasoning_endpoints():
    # 回归：端点绑定思考类模型时思考不收敛、吃满 max_tokens（实测
    # reasoning_tokens=4096 → LengthFinishReasonError）。必须在请求体关闭思考。
    llm = get_agent_llm("evaluator")
    assert llm.extra_body == {"thinking": {"type": "disabled"}}


def test_get_agent_llm_uses_agent_endpoint_override(monkeypatch):
    monkeypatch.setenv("ARK_INTERVIEWER_ENDPOINT_ID", "ep-interviewer")
    assert get_agent_llm("interviewer").model_name == "ep-interviewer"


def test_get_agent_llm_unknown_agent_raises():
    with pytest.raises(ValueError, match="未知 Agent"):
        get_agent_llm("no_such_agent")


def test_get_vision_llm_requires_endpoint(monkeypatch):
    monkeypatch.setattr(Config, "ARK_VISION_ENDPOINT_ID", None)
    with pytest.raises(ValueError, match="ARK_VISION_ENDPOINT_ID"):
        get_vision_llm()
    monkeypatch.setattr(Config, "ARK_VISION_ENDPOINT_ID", "ep-vision")
    assert get_vision_llm().model_name == "ep-vision"


def test_get_agent_llm_requires_endpoint(monkeypatch):
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", None)
    monkeypatch.delenv("ARK_INTERVIEWER_ENDPOINT_ID", raising=False)
    with pytest.raises(ValueError, match="端点未配置"):
        get_agent_llm("interviewer")
