"""pytest 全局配置：关闭外部追踪、注入 sys.path 与通用 fixture。"""
import os
import sys
from pathlib import Path

# 必须在任何 LangChain/LangGraph 模块导入前关闭，测试不得向外部服务发送数据。
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("RTC_CALLBACK_SECRET", "test-callback-secret")

# 让 pytest 能 import config / services / agents / api / mcp / rag（rag_llm_server 为包根）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 追加仓库根：使 `rag_llm_server.xxx` 前缀导入可用（rag_llm_server 无 __init__.py，
# 以命名空间包解析；append 保证 `import config` 等平铺导入仍优先命中 rag_llm_server 目录）
sys.path.append(str(Path(__file__).resolve().parents[2]))

import pytest


@pytest.fixture
def tmp_storage(tmp_path):
    """SqliteStorage fixture（Task 2 落地后生效；此处先占位防 Task 1 测试报错）。"""
    from services.storage.sqlite import SqliteStorage  # Task 2 提供

    async def _make():
        s = SqliteStorage(str(tmp_path / "test.db"))
        await s.init()
        return s

    return _make
