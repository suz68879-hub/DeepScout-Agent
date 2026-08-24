"""RAG 检索接口（spec §5.9）：RAG_PROVIDER 切换 llamaindex（默认）/ volc_kb。

统一异步接口 aretrieve(query) -> str：检索上下文文本，无命中返回空串。
"""
from typing import Protocol

from config import settings


class Retriever(Protocol):
    async def aretrieve(self, query: str, top_k: int = 3) -> str: ...


def get_retriever() -> Retriever:
    if settings.RAG_PROVIDER == "volc_kb":
        from .volc_kb_provider import VolcKbProvider

        return VolcKbProvider()
    from .llamaindex_provider import LlamaIndexProvider

    return LlamaIndexProvider()
