"""LLMService 凭证缺失时的降级契约。

回归背景：CI 不注入 ARK_API_KEY。顶层 import services.llm_service 会构造模块级
单例，旧实现在 Ark(api_key=None) 处直接 AssertionError，导致 pytest 收集中断。
本用例固化“无凭证时优雅降级、不在构造期崩溃”的契约（与 RagService 一致）。
"""
from config import settings
from services.llm_service import LLMService


def _simulate_missing_credentials(monkeypatch):
    # Ark SDK 在 api_key 为空时会回退读取环境变量，需一并清除才能复现 CI（无凭证）。
    monkeypatch.setattr(settings, "ARK_API_KEY", None)
    for env_key in ("ARK_API_KEY", "VOLC_ACCESSKEY", "VOLC_SECRETKEY"):
        monkeypatch.delenv(env_key, raising=False)


def test_instantiation_without_api_key_does_not_raise(monkeypatch):
    _simulate_missing_credentials(monkeypatch)

    service = LLMService()

    assert service.client is None


def test_chat_stream_yields_config_error_when_client_missing(monkeypatch):
    _simulate_missing_credentials(monkeypatch)
    service = LLMService()

    result = list(service.chat_stream([{"role": "user", "content": "hi"}]))

    assert result == ["服务配置错误"]
