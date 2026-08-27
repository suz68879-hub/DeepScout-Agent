import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import Config, settings
from observability.instrumentation import instrument_sqlalchemy
from observability.metrics import service_metrics

logger = logging.getLogger(__name__)


class DatabaseRuntime:
    """持有共享 Engine 和会话工厂，但不共享 AsyncSession。"""

    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory

    async def check_connection(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def start(self) -> None:
        try:
            await self.check_connection()
        except BaseException:
            await self.close()
            raise

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def close(self) -> None:
        await self.engine.dispose()


def build_database_runtime(config: Config) -> DatabaseRuntime:
    engine = create_async_engine(
        config.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=config.DATABASE_POOL_SIZE,
        max_overflow=config.DATABASE_MAX_OVERFLOW,
        pool_timeout=config.DATABASE_POOL_TIMEOUT,
        pool_recycle=config.DATABASE_POOL_RECYCLE,
    )
    instrument_sqlalchemy(engine)
    service_metrics.set_db_pool_capacity(
        config.DATABASE_POOL_SIZE + config.DATABASE_MAX_OVERFLOW
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return DatabaseRuntime(engine, session_factory)


_runtime: DatabaseRuntime | None = None


async def init_database(config: Config = settings) -> DatabaseRuntime | None:
    global _runtime
    if config.STORAGE_BACKEND != "postgres":
        return None
    if _runtime is not None:
        return _runtime

    runtime = build_database_runtime(config)
    await runtime.start()
    _runtime = runtime
    logger.info(
        "PostgreSQL connection pool initialized",
        extra={"event": "database_pool_started", "database": config.database_log_target()},
    )
    return runtime


def get_database_runtime() -> DatabaseRuntime:
    if _runtime is None:
        raise RuntimeError("PostgreSQL database runtime is not initialized")
    return _runtime


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_database_runtime().session_scope() as session:
        yield session


async def close_database() -> None:
    global _runtime
    runtime = _runtime
    _runtime = None
    if runtime is None:
        return
    try:
        await runtime.close()
    except BaseException:
        logger.exception(
            "Failed to close PostgreSQL connection pool",
            extra={
                "event": "database_pool_close_failed",
                "error_code": "DATABASE_POOL_CLOSE_FAILED",
            },
        )
        raise
