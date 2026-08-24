"""本地 LlamaIndex 检索（默认 provider，演示零云端检索依赖）。

索引构建见 build_index.py；检索按 RAG_SIMILARITY_THRESHOLD 过滤（spec §5.9）。
"""
import asyncio
from pathlib import Path

from llama_index.core import StorageContext, load_index_from_storage

from config import settings
from rag.build_index import build_embedding

INDEX_DIR = Path(__file__).resolve().parent / "storage_index"


class LlamaIndexProvider:
    def __init__(self, index_dir: Path = INDEX_DIR):
        self.index_dir = index_dir
        self._index = None

    def _load(self):
        if self._index is None:
            if not (self.index_dir / "docstore.json").exists():
                raise FileNotFoundError("本地索引不存在，请先运行 rag/build_index.py")
            # 持久化不存 embed 模型配置，必须显式注入与建库一致的 embedding 客户端，
            # 否则回落 Settings.embed_model（默认 OpenAI 端点），查询向量打错厂商
            self._index = load_index_from_storage(
                StorageContext.from_defaults(persist_dir=str(self.index_dir)),
                embed_model=build_embedding(),
            )
        return self._index

    def retrieve(self, query: str, top_k: int = 3) -> str:
        """同步检索：top-k + 相似度阈值过滤 + 编号格式化。"""
        nodes = self._load().as_retriever(similarity_top_k=top_k).retrieve(query)
        kept = [
            n for n in nodes
            if n.score is not None and n.score >= settings.RAG_SIMILARITY_THRESHOLD
        ]
        if not kept:
            return ""
        return "\n".join(f"【参考 {i + 1}】{n.get_content()}" for i, n in enumerate(kept))

    async def aretrieve(self, query: str, top_k: int = 3) -> str:
        """异步包装：本地检索移入线程池，避免阻塞事件循环。"""
        return await asyncio.to_thread(self.retrieve, query, top_k)
