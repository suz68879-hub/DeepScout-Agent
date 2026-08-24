"""题库索引构建脚本（幂等，spec §5.9）。

用法（rag_llm_server 目录下）：
    uv run python -m rag.build_index
"""
from pathlib import Path

from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from llama_index.embeddings.openai import OpenAIEmbedding

from config import settings

QUESTION_BANK = Path(__file__).resolve().parent / "data" / "question_bank"
INDEX_DIR = Path(__file__).resolve().parent / "storage_index"


def build_embedding() -> OpenAIEmbedding:
    """构造 OpenAI 兼容 embedding 客户端（方舟/百炼等，见 EMBEDDING_* 配置）。

    llama-index 0.3.x 对 model= 做 OpenAIEmbeddingModelType 枚举校验，接入点 ID
    或第三方模型名必须经 model_name= 传入（作为真实请求的 model 字段）。
    """
    api_base, api_key, model = settings.embedding_config()
    # embed_batch_size=10：百炼 /embeddings 单次最多 10 条输入（方舟亦兼容）
    return OpenAIEmbedding(
        api_key=api_key,
        api_base=api_base,
        model_name=model,
        embed_batch_size=10,
    )


def main() -> None:
    _, _, model = settings.embedding_config()
    if not model:
        raise SystemExit("[RAG] 未配置 EMBEDDING_MODEL（或 ARK_EMBEDDING_ENDPOINT_ID），无法构建索引")
    docs = SimpleDirectoryReader(str(QUESTION_BANK), recursive=True).load_data()
    embed = build_embedding()
    index = VectorStoreIndex.from_documents(docs, embed_model=embed)
    index.storage_context.persist(persist_dir=str(INDEX_DIR))
    print(f"[RAG] 索引构建完成：{len(docs)} 篇文档 → {INDEX_DIR}")


if __name__ == "__main__":
    main()
