"""config.py 回落与解析逻辑测试。"""
from rag_llm_server.config import Config, _bool_env, _csv_env, _float_env  # noqa: F401


def test_float_env_default_when_unset():
    assert _float_env("ARK_NO_SUCH_KEY_12345", 0.35) == 0.35


def test_float_env_parses(monkeypatch):
    monkeypatch.setenv("RAG_SIMILARITY_THRESHOLD", "0.5")
    assert _float_env("RAG_SIMILARITY_THRESHOLD", 0.35) == 0.5


def test_float_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("RAG_SIMILARITY_THRESHOLD", "abc")
    assert _float_env("RAG_SIMILARITY_THRESHOLD", 0.35) == 0.35


def test_bool_env_defaults_and_parses(monkeypatch):
    monkeypatch.delenv("ENABLE_DEBUG_ROUTES", raising=False)
    assert _bool_env("ENABLE_DEBUG_ROUTES", False) is False
    monkeypatch.setenv("ENABLE_DEBUG_ROUTES", "true")
    assert _bool_env("ENABLE_DEBUG_ROUTES", False) is True


def test_csv_env_defaults_and_ignores_blank_items(monkeypatch):
    default = ("http://localhost:3000", "http://127.0.0.1:3000")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert _csv_env("CORS_ORIGINS", default) == default
    monkeypatch.setenv("CORS_ORIGINS", "https://one.example, ,https://two.example")
    assert _csv_env("CORS_ORIGINS", default) == (
        "https://one.example", "https://two.example",
    )


def test_agent_endpoint_fallback_to_default(monkeypatch):
    # R-T1-1：ARK_ENDPOINT_ID 在 Config 类定义时绑定（启动读 .env 语义），用 setattr 注入
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    monkeypatch.delenv("ARK_INTERVIEWER_ENDPOINT_ID", raising=False)
    assert Config().agent_endpoint_id("interviewer") == "ep-default"


def test_agent_endpoint_empty_string_falls_back_to_default(monkeypatch):
    # 空字符串视为未配置（.env.example 复制出的状态），回落默认端点
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    monkeypatch.setenv("ARK_INTERVIEWER_ENDPOINT_ID", "")
    assert Config().agent_endpoint_id("interviewer") == "ep-default"


def test_agent_endpoint_override(monkeypatch):
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    monkeypatch.setenv("ARK_INTERVIEWER_ENDPOINT_ID", "ep-interviewer")
    assert Config().agent_endpoint_id("interviewer") == "ep-interviewer"


def test_agent_endpoint_unknown_agent_falls_back(monkeypatch):
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    assert Config().agent_endpoint_id("no_such_agent") == "ep-default"
