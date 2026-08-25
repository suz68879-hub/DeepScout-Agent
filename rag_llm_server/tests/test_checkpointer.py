from types import SimpleNamespace


async def test_postgres_checkpointer_uses_independent_pool(monkeypatch):
    import agents.graph as graph_module

    events = []

    class FakeConnection:
        async def execute(self, statement):
            events.append(str(statement))

    class FakeConnectionContext:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class FakePool:
        def __init__(self, **kwargs):
            events.append(kwargs)

        async def open(self):
            events.append("open")

        def connection(self):
            return FakeConnectionContext()

        async def close(self):
            events.append("close")

    class FakeSaver:
        def __init__(self, pool):
            self.pool = pool

    config = SimpleNamespace(
        STORAGE_BACKEND="postgres",
        DATABASE_URL="postgresql+psycopg://app:secret@db/checkpoints",
        DATABASE_POOL_SIZE=4,
    )
    monkeypatch.setattr(graph_module, "AsyncConnectionPool", FakePool, raising=False)
    monkeypatch.setattr(graph_module, "AsyncPostgresSaver", FakeSaver, raising=False)
    monkeypatch.setattr(graph_module, "_checkpoint_pool", None, raising=False)

    saver = await graph_module.make_checkpointer(config)

    assert isinstance(saver, FakeSaver)
    assert events[0]["conninfo"] == "postgresql://app:secret@db/checkpoints"
    assert events[0]["max_size"] == 4
    assert events[1:] == ["open", "SELECT 1 FROM checkpoint_migrations LIMIT 1"]
