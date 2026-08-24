"""本地 mcp 工具包（spec §5.8）。

注意：本包与官方 mcp SDK 同名（site-packages/mcp）。
extend_path 合并两条 __path__，使 `mcp.sqlite_server`（本地）与
`mcp.server.fastmcp`（SDK）在 rag_llm_server 作为 cwd/sys.path 根时同时可导入；
否则本地空 __init__.py 会遮蔽 SDK 的 mcp.server 子包。
"""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
