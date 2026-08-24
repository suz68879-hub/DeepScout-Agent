import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from db.models import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

load_dotenv()
database_url = os.getenv("MIGRATION_DATABASE_URL")
if not database_url:
    raise RuntimeError("MIGRATION_DATABASE_URL is required for Alembic")
if not database_url.startswith("postgresql+psycopg://"):
    raise RuntimeError("MIGRATION_DATABASE_URL must use postgresql+psycopg://")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif os.name == "nt":
    asyncio.run(run_async_migrations(), loop_factory=asyncio.SelectorEventLoop)
else:
    asyncio.run(run_async_migrations())
