"""应用生命周期：graph 单例与 checkpointer 连接由 FastAPI lifespan 管理。"""


async def test_graph_init_is_idempotent(monkeypatch):
    import agents.graph as graph_module

    built_graph = object()
    build_calls = 0

    async def fake_build_graph():
        nonlocal build_calls
        build_calls += 1
        return built_graph

    monkeypatch.setattr(graph_module, "_graph", None)
    monkeypatch.setattr(graph_module, "build_graph", fake_build_graph)

    first = await graph_module.init_graph()
    second = await graph_module.init_graph()

    assert first is built_graph
    assert second is built_graph
    assert graph_module.get_graph() is built_graph
    assert build_calls == 1


async def test_graph_close_releases_checkpointer_connection(monkeypatch):
    import agents.graph as graph_module

    class FakeConnection:
        closed = False

        async def close(self):
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(graph_module, "_graph", object())
    monkeypatch.setattr(graph_module, "_checkpoint_conn", connection)

    await graph_module.close_graph()

    assert connection.closed is True
    assert graph_module._graph is None
    assert graph_module._checkpoint_conn is None


async def test_graph_close_releases_postgres_checkpointer_pool(monkeypatch):
    import agents.graph as graph_module

    class FakePool:
        closed = False

        async def close(self):
            self.closed = True

    pool = FakePool()
    monkeypatch.setattr(graph_module, "_graph", object())
    monkeypatch.setattr(graph_module, "_checkpoint_conn", None)
    monkeypatch.setattr(graph_module, "_checkpoint_pool", pool, raising=False)

    await graph_module.close_graph()

    assert pool.closed is True
    assert graph_module._checkpoint_pool is None
