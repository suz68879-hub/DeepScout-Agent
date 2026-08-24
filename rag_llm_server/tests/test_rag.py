"""RAG：阈值过滤、格式化、provider 切换与 volc_kb 包装。"""
import pytest

from config import settings
from rag.llamaindex_provider import LlamaIndexProvider
from rag.provider import get_retriever
from rag.volc_kb_provider import VolcKbProvider


class FakeNode:
    def __init__(self, text, score):
        self._text = text
        self.score = score

    def get_content(self):
        return self._text


class FakeIndex:
    def __init__(self, nodes):
        self.nodes = nodes

    def as_retriever(self, similarity_top_k=3):
        self.last_top_k = similarity_top_k
        return self

    def retrieve(self, query):
        return self.nodes


def _provider(nodes, threshold=None):
    p = LlamaIndexProvider()
    p._index = FakeIndex(nodes)
    if threshold is not None:
        from config import settings as s
        s.RAG_SIMILARITY_THRESHOLD = threshold
    return p


def test_retrieve_filters_below_threshold():
    p = _provider(
        [FakeNode("命中A", 0.9), FakeNode("低于阈值B", 0.1), FakeNode("命中C", 0.5)],
        threshold=0.35,
    )
    result = p.retrieve("查询", top_k=3)
    assert "命中A" in result and "命中C" in result
    assert "低于阈值B" not in result


def test_retrieve_empty_when_all_below_threshold():
    p = _provider([FakeNode("低分", 0.1)], threshold=0.35)
    assert p.retrieve("查询") == ""


def test_retrieve_formats_numbered_references():
    p = _provider([FakeNode("第一段", 0.8), FakeNode("第二段", 0.7)], threshold=0.35)
    result = p.retrieve("查询")
    assert result == "【参考 1】第一段\n【参考 2】第二段"


def test_aretrieve_runs_in_threadpool():
    import asyncio
    p = _provider([FakeNode("段落", 0.8)], threshold=0.35)
    assert asyncio.run(p.aretrieve("查询")) == "【参考 1】段落"


def test_load_injects_configured_embed_model(tmp_path, monkeypatch):
    # 回归：llama-index 0.3.x 持久化不存 embed 模型配置，load 时若未显式传入，
    # 会回落 Settings.embed_model（默认 OpenAI 端点）——查询向量打错厂商且维度不符。
    from llama_index.core import Document, VectorStoreIndex
    from llama_index.core.base.embeddings.base import BaseEmbedding

    class FakeEmbedding(BaseEmbedding):
        def _get_query_embedding(self, query):
            return [0.0] * 8

        async def _aget_query_embedding(self, query):
            return [0.0] * 8

        def _get_text_embedding(self, text):
            return [0.0] * 8

    index = VectorStoreIndex.from_documents(
        [Document(text="样本")], embed_model=FakeEmbedding()
    )
    index.storage_context.persist(persist_dir=str(tmp_path))

    monkeypatch.setenv("EMBEDDING_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")

    embed = LlamaIndexProvider(index_dir=tmp_path)._load()._embed_model

    assert embed.model_name == "text-embedding-v4"
    assert embed.api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_provider_switch_by_env(monkeypatch):
    monkeypatch.setattr(settings, "RAG_PROVIDER", "volc_kb")
    assert isinstance(get_retriever(), VolcKbProvider)
    monkeypatch.setattr(settings, "RAG_PROVIDER", "llamaindex")
    assert isinstance(get_retriever(), LlamaIndexProvider)


def test_volc_kb_delegates_to_rag_service(monkeypatch):
    import asyncio
    import rag.volc_kb_provider as vp_mod

    class FakeRag:
        async def retrieve(self, query):
            return f"kb:{query}"

    monkeypatch.setattr(vp_mod, "rag_service", FakeRag())
    p = VolcKbProvider()
    assert asyncio.run(p.aretrieve("你好")) == "kb:你好"
