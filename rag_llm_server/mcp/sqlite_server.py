"""只读 SQLite MCP server（spec §5.8 协议层安全）。

安全边界：validate_query 是唯一安全边界（HTTP API 与 MCP 协议两条路径共用）。
- 单语句 / 仅 SELECT / 白名单表 / 禁写关键词（含 union）/ 强制 LIMIT ≤ 100
- 执行层再加只读连接（mode=ro）双保险
"""
import re
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from config import settings

ALLOWED_TABLES = {"resume", "interview_session", "message", "interview_report"}
FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "attach", "pragma", "union",
)
MAX_LIMIT = 100

mcp = FastMCP("interview-sqlite")

_FROM_CLAUSE_RE = re.compile(
    r"\bfrom\s+(.+?)(?=\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)"
)
_LIMIT_RE = re.compile(r"\blimit\s+(-?\d+)(?:\s*,\s*(-?\d+))?")


def _mask_literals_and_comments(sql_text: str) -> tuple[str, int]:
    """把字符串字面量（'…'，含 '' 转义）与注释（-- 行注释、/*…*/）替换为等长空格。

    返回 (masked, trailing_comment_start)：后者为吞到行尾/EOF 的最后一个 -- 注释
    起始下标，无则为 -1。用于强制 LIMIT 的插入位置，避免追加进行注释内部。
    """
    out = []
    trailing_comment_start = -1
    i, n = 0, len(sql_text)
    while i < n:
        c = sql_text[i]
        if c == "'":
            j = i + 1
            while j < n:
                if sql_text[j] == "'":
                    if j + 1 < n and sql_text[j + 1] == "'":  # '' 转义
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(" " * (j - i))
            i = j
        elif c == "-" and i + 1 < n and sql_text[i + 1] == "-":
            j = sql_text.find("\n", i)
            if j == -1:
                j = n
                trailing_comment_start = i
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and sql_text[i + 1] == "*":
            j = sql_text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out), trailing_comment_start


def validate_query(sql: str) -> str:
    """校验并规范化查询；不合法抛 ValueError（协议层拒绝）。"""
    stmt = sql.strip().rstrip(";")
    statements = [s.strip() for s in stmt.split(";") if s.strip()]
    if len(statements) != 1:
        raise ValueError("仅支持单语句查询")
    s = statements[0]
    s_lower = s.lower()
    if not s_lower.startswith("select"):
        raise ValueError("仅支持 SELECT 只读查询")
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", s_lower):
            raise ValueError(f"禁止关键词: {kw}")
    tables = set(re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", s_lower))
    if not tables:
        raise ValueError("未检测到 FROM 子句")
    # R-T14-2：FROM 子句含逗号即多表查询，直接拒绝——数据分析仅需单表；
    # 逗号多表只会被上方正则提取首个表名（如 resume, checkpoint 仅提取 resume），
    # 可绕过白名单检查（checkpoint 与业务表同库）。join 链多表已由 join 正则覆盖。
    from_clause = _FROM_CLAUSE_RE.search(s_lower)
    if from_clause and "," in re.sub(r"\([^)]*\)", "", from_clause.group(1)):
        raise ValueError("不支持多表查询（仅允许单表 FROM）")
    if not tables.issubset(ALLOWED_TABLES):
        raise ValueError(f"表名不在白名单: {sorted(tables - ALLOWED_TABLES)}")
    # R-T14-3：LIMIT 分析在剥离字面量/注释后的文本上进行，绝不改写字面量内容。
    # 解析 LIMIT count 与 LIMIT offset, count 两形态；count > MAX 或任一负数 → 拒绝。
    masked, trailing_comment_start = _mask_literals_and_comments(s_lower)
    limits = list(_LIMIT_RE.finditer(masked))
    if not limits:
        # 无 LIMIT 强制追加；行注释吞到 EOF 时插到注释之前，否则追加在行注释内无效
        insert_at = trailing_comment_start if trailing_comment_start != -1 else len(s)
        s = f"{s[:insert_at]} LIMIT {MAX_LIMIT}{s[insert_at:]}"
    else:
        for lm in limits:
            if lm.group(2) is None:
                count, offset = int(lm.group(1)), 0
            else:
                offset, count = int(lm.group(1)), int(lm.group(2))
            if offset < 0 or count < 0:
                raise ValueError(f"LIMIT 不能为负: {lm.group(0)}")
            if count > MAX_LIMIT:
                raise ValueError(f"LIMIT 超出上限: {lm.group(0)} > {MAX_LIMIT}")
    return s


def query(sql: str, user_id: str) -> list[dict]:
    """执行只读 SQL 查询（协议层校验 + 只读连接双保险）。"""
    safe_sql = validate_query(sql)
    # Windows 绝对路径（反斜杠）在 file: URI 下跨环境不可靠，统一 as_posix 归一
    uri = f"file:{Path(settings.DATABASE_PATH).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.create_function("current_user_id", 0, lambda: user_id, deterministic=True)
        conn.executescript("""
        CREATE TEMP VIEW resume AS
          SELECT id, content, structured_json, source, status, created_at, updated_at
          FROM main.resume WHERE user_id = current_user_id();
        CREATE TEMP VIEW interview_session AS
          SELECT id, resume_id, position, stage, status, started_at, ended_at
          FROM main.interview_session WHERE user_id = current_user_id();
        CREATE TEMP VIEW message AS
          SELECT m.id, m.session_id, m.role, m.content, m.seq, m.created_at
          FROM main.message m JOIN main.interview_session s ON s.id = m.session_id
          WHERE s.user_id = current_user_id();
        CREATE TEMP VIEW interview_report AS
          SELECT id, session_id, scores_json, feedback_json, suggestions_json,
                 position, source, md_path, created_at
          FROM main.interview_report WHERE user_id = current_user_id();
        """)
        conn.execute("PRAGMA query_only = ON")
        return [dict(r) for r in conn.execute(safe_sql).fetchall()]
    finally:
        conn.close()


@mcp.tool()
def query_tool(sql: str) -> list[dict]:
    """对面试陪练数据库执行只读 SQL 查询（SELECT + 白名单表 + LIMIT ≤ 100）。"""
    raise ValueError("direct MCP queries are disabled; use the authenticated analytics API")


if __name__ == "__main__":
    mcp.run()
