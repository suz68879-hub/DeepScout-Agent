import asyncio

import pytest

from config import Config
from db import engine as engine_module


class FakeConnection:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.statements = []

    async def __aenter__(self):
        if self.fail:
            raise ConnectionError("database unavailable")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, statement):
        self.statements.append(str(statement))


class FakeEngine:
    def __init__(self, *, fail_connect=False):
        self.connection = FakeConnection(fail=fail_connect)
        self.disposed = False

    def connect(self):
        return self.connection

    async def dispose(self):
        self.disposed = True


class FakeSession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True
        return False

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class FakeSessionFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        session = FakeSession()
        self.sessions.append(session)
        return session


def _postgres_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://deepscout_app:test-only@localhost/deepscout_test",
    )
    monkeypatch.setenv("DATABASE_POOL_SIZE", "6")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "3")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT", "11")
    monkeypatch.setenv("DATABASE_POOL_RECYCLE", "700")
    return Config()


def test_build_runtime_uses_pool_configuration(monkeypatch):
    config = _postgres_config(monkeypatch)
    fake_engine = FakeEngine()
    captured = {}

    def fake_create_async_engine(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return fake_engine

    def fake_async_sessionmaker(engine, **kwargs):
        captured["session_engine"] = engine
        captured["session_options"] = kwargs
        return FakeSessionFactory()

    monkeypatch.setattr(engine_module, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(engine_module, "async_sessionmaker", fake_async_sessionmaker)

    runtime = engine_module.build_database_runtime(config)

    assert runtime.engine is fake_engine
    assert captured == {
        "url": config.DATABASE_URL,
        "pool_pre_ping": True,
        "pool_size": 6,
        "max_overflow": 3,
        "pool_timeout": 11,
        "pool_recycle": 700,
        "session_engine": fake_engine,
        "session_options": {"expire_on_commit": False},
    }


async def test_runtime_checks_connection_and_disposes_pool():
    fake_engine = FakeEngine()
    runtime = engine_module.DatabaseRuntime(fake_engine, FakeSessionFactory())

    await runtime.check_connection()
    await runtime.close()

    assert fake_engine.connection.statements == ["SELECT 1"]
    assert fake_engine.disposed is True


async def test_failed_connection_check_disposes_pool():
    fake_engine = FakeEngine(fail_connect=True)
    runtime = engine_module.DatabaseRuntime(fake_engine, FakeSessionFactory())

    with pytest.raises(ConnectionError, match="database unavailable"):
        await runtime.start()

    assert fake_engine.disposed is True


async def test_session_scope_commits_and_closes():
    factory = FakeSessionFactory()
    runtime = engine_module.DatabaseRuntime(FakeEngine(), factory)

    async with runtime.session_scope() as session:
        assert session is factory.sessions[0]

    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True


async def test_session_scope_rolls_back_and_preserves_exception():
    factory = FakeSessionFactory()
    runtime = engine_module.DatabaseRuntime(FakeEngine(), factory)

    with pytest.raises(LookupError, match="business failure"):
        async with runtime.session_scope() as session:
            raise LookupError("business failure")

    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True


async def test_concurrent_scopes_use_distinct_sessions():
    factory = FakeSessionFactory()
    runtime = engine_module.DatabaseRuntime(FakeEngine(), factory)

    async def use_session():
        async with runtime.session_scope() as session:
            await asyncio.sleep(0)
            return session

    first, second = await asyncio.gather(use_session(), use_session())

    assert first is not second
    assert all(session.committed and session.closed for session in (first, second))


async def test_global_runtime_initializes_once_and_closes(monkeypatch):
    config = _postgres_config(monkeypatch)
    runtime = engine_module.DatabaseRuntime(FakeEngine(), FakeSessionFactory())
    start_calls = 0

    async def fake_start():
        nonlocal start_calls
        start_calls += 1

    monkeypatch.setattr(runtime, "start", fake_start)
    monkeypatch.setattr(engine_module, "_runtime", None)
    monkeypatch.setattr(engine_module, "build_database_runtime", lambda _config: runtime)

    first = await engine_module.init_database(config)
    second = await engine_module.init_database(config)
    await engine_module.close_database()

    assert first is runtime
    assert second is runtime
    assert start_calls == 1
    assert runtime.engine.disposed is True
    assert engine_module._runtime is None
