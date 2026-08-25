"""PostgreSQL 只读分析执行器：AST 白名单与数据库只读事务双重限制。"""
import uuid
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from sqlglot import ErrorLevel, expressions as exp, parse

from config import settings


ALLOWED_TABLES = {"resume", "interview_session", "message", "interview_report"}
FORBIDDEN_FUNCTIONS = {"dblink", "lo_export", "lo_import", "pg_read_file", "pg_sleep"}
MAX_ROWS = 500


@dataclass(frozen=True)
class QueryResult:
    items: list[dict]
    truncated: bool


def validate_query(sql: str) -> str:
    raw = sql.strip()
    if not raw:
        raise ValueError("query cannot be empty")
    if "--" in raw or "/*" in raw or "*/" in raw:
        raise ValueError("SQL comments are not allowed")
    try:
        statements = [statement for statement in parse(raw, read="postgres", error_level=ErrorLevel.RAISE) if statement]
    except Exception:
        raise ValueError("invalid PostgreSQL query") from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("only one SELECT or CTE query is allowed")
    tree = statements[0]
    forbidden_types = tuple(
        node_type
        for node_type in (
            exp.Alter,
            exp.Command,
            exp.Create,
            exp.Delete,
            exp.Drop,
            exp.Insert,
            exp.Into,
            exp.Lock,
            exp.Merge,
            exp.Update,
        )
        if node_type is not None
    )
    if any(isinstance(node, forbidden_types) for node in tree.walk()):
        raise ValueError("query contains a forbidden operation")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = list(tree.find_all(exp.Table))
    for table in tables:
        name = table.name.lower()
        if table.catalog or table.db:
            raise ValueError("schema-qualified tables are not allowed")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise ValueError("table is not in the analytics whitelist")
    if not tables:
        raise ValueError("query must read an analytics table")

    for function in tree.find_all(exp.Func):
        function_name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if function_name.lower() in FORBIDDEN_FUNCTIONS:
            raise ValueError("function is not allowed")

    limit = tree.args.get("limit")
    if limit is not None:
        expression = limit.expression
        if not isinstance(expression, exp.Literal) or not expression.is_int:
            raise ValueError("LIMIT must be an integer")
        count = int(expression.this)
        if count < 0 or count > MAX_ROWS:
            raise ValueError("LIMIT exceeds the maximum")
    else:
        tree = tree.limit(MAX_ROWS)
    return tree.sql(dialect="postgres")


async def query(sql: str, user_id: str, database_url: str | None = None) -> QueryResult:
    safe_sql = validate_query(sql)
    analytics_url = database_url or settings.ANALYTICS_DATABASE_URL
    if not analytics_url:
        raise RuntimeError("ANALYTICS_DATABASE_URL is required")
    tenant_id = uuid.UUID(user_id)
    conninfo = analytics_url.replace("postgresql+psycopg://", "postgresql://", 1)
    async with await AsyncConnection.connect(
        conninfo,
        autocommit=False,
        connect_timeout=5,
        row_factory=dict_row,
    ) as connection:
        async with connection.transaction():
            await connection.execute("SET TRANSACTION READ ONLY")
            await connection.execute("SET LOCAL statement_timeout = '5s'")
            await connection.execute("SET LOCAL search_path = analytics, pg_catalog")
            await connection.execute(
                "SELECT set_config('app.user_id', %s, true)", (str(tenant_id),)
            )
            cursor = await connection.execute(safe_sql)
            rows = await cursor.fetchmany(MAX_ROWS)
    items = [dict(row) for row in rows]
    return QueryResult(items=items, truncated=len(items) == MAX_ROWS)
