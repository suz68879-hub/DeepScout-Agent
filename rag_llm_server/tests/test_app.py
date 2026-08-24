from fastapi.middleware.cors import CORSMiddleware

import main


def _paths(app):
    return {route.path for route in app.routes}


def test_debug_routes_are_disabled_by_default(monkeypatch):
    monkeypatch.setattr(main.settings, "ENABLE_DEBUG_ROUTES", False)
    assert "/debug/chat" not in _paths(main.create_app())
    assert "/debug/rag" not in _paths(main.create_app())


def test_debug_routes_can_be_enabled(monkeypatch):
    monkeypatch.setattr(main.settings, "ENABLE_DEBUG_ROUTES", True)
    paths = _paths(main.create_app())
    assert "/debug/chat" in paths
    assert "/debug/rag" in paths


def test_cors_uses_configured_origins_with_credentials(monkeypatch):
    origins = ("https://one.example", "https://two.example")
    monkeypatch.setattr(main.settings, "CORS_ORIGINS", origins)
    app = main.create_app()
    middleware = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    assert middleware.kwargs["allow_origins"] == list(origins)
    assert middleware.kwargs["allow_credentials"] is True


async def test_lifespan_initializes_and_closes_resources(monkeypatch):
    events = []

    async def record(name):
        events.append(name)

    monkeypatch.setattr(main, "init_storage", lambda: record("init_storage"))
    monkeypatch.setattr(main, "init_graph", lambda: record("init_graph"))
    monkeypatch.setattr(main, "shutdown_cold_tasks", lambda: record("shutdown_cold_tasks"))
    monkeypatch.setattr(main, "close_graph", lambda: record("close_graph"))
    monkeypatch.setattr(main, "close_storage", lambda: record("close_storage"))
    monkeypatch.setattr(main.registry, "render_all", lambda: None)
    monkeypatch.setattr("services.storage.get_tos_store", lambda: None)

    async with main.lifespan(main.create_app()):
        assert events == ["init_storage", "init_graph"]

    assert events == [
        "init_storage", "init_graph", "shutdown_cold_tasks", "close_graph", "close_storage",
    ]
