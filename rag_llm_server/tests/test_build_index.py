"""build_index 脚本测试：OpenAI 兼容 embedding 多厂商配置（方舟/百炼等）。"""
import pytest
from llama_index.embeddings.openai import OpenAIEmbedding

from config import ARK_BASE_URL, settings
from rag.build_index import build_embedding


def test_build_embedding_uses_configured_openai_compat_settings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-dashscope-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")

    embed = build_embedding()

    assert embed.model_name == "text-embedding-v4"
    assert embed.api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert embed.api_key == "test-dashscope-key"


def test_embedding_config_falls_back_to_ark(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_BASE", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("ARK_EMBEDDING_ENDPOINT_ID", "ep-ark-default")

    api_base, api_key, model = settings.embedding_config()

    assert api_key == "ark-key"
    assert model == "ep-ark-default"
    assert api_base == ARK_BASE_URL


def test_endpoint_id_as_model_is_rejected_by_enum():
    # 回归文档：llama-index 0.3.x 对 model= 做 OpenAIEmbeddingModelType 枚举校验，
    # 直接传 ep- ID 会 ValueError——故 build_embedding 必须走 model_name=。
    with pytest.raises(ValueError):
        OpenAIEmbedding(api_key="test-key", model="ep-test-endpoint")


def test_build_embedding_respects_dashscope_batch_limit(monkeypatch):
    # 百炼 /embeddings 单次请求最多 10 条输入（实测 400：batch size should not
    # be larger than 10）——embed_batch_size 必须 <= 10，否则批量入库必失败。
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")

    embed = build_embedding()

    assert embed.embed_batch_size <= 10
