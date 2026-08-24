"""火山云知识库检索（现有 rag_service 能力迁入，spec §5.9）。"""
from services.rag_service import rag_service


class VolcKbProvider:
    """包装现有火山云知识库能力；返回字符串上下文（无命中为空串）。"""

    async def aretrieve(self, query: str, top_k: int = 3) -> str:
        return await rag_service.retrieve(query)
