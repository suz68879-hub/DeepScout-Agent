"""一次性初始化 LangGraph PostgreSQL checkpointer；应用启动不得调用。"""
import asyncio
import os

from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


CHECKPOINT_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


async def setup() -> None:
    load_dotenv()
    database_url = os.getenv("MIGRATION_DATABASE_URL")
    if not database_url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required")
    conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()
        for table in CHECKPOINT_TABLES:
            await saver.conn.execute(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" TO deepscout_app'
            )


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.run(setup(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(setup())
