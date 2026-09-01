from fastapi.middleware.cors import CORSMiddleware
import httpx
import pytest

import main


def _paths(app):
    return {route.path for route in app.routes}


def test_debug_routes_are_disabled_by_default(monkeypatch):
    monkeypatch.setattr(main.settings, "ENABLE_DEBUG_ROUTES", False)
    assert "/debug/chat" not in _paths(main.create_app())
    assert "/debug/rag" not in _paths(main.create_app())


def test_debug_routes_can_be_enabled(monkeypatch):
    monkeypatch.setattr(main.settings, "ENABLE_DEBUG_ROUTES", True)
    monkeypatch.setattr(main.settings, "APP_ENV", "development", raising=False)
    paths = _paths(main.create_app())
    assert "/debug/chat" in paths
    assert "/debug/rag" in paths


def test_debug_routes_stay_off_in_production(monkeypatch):
    monkeypatch.setattr(main.settings, "ENABLE_DEBUG_ROUTES", True)
    monkeypatch.setattr(main.settings, "APP_ENV", "production", raising=False)
    paths = _paths(main.create_app())
    assert "/debug/chat" not in paths
    assert "/debug/rag" not in paths


def test_public_job_query_route_is_registered():
    assert "/api/jobs/{job_id}" in _paths(main.create_app())


def test_cors_uses_configured_origins_with_credentials(monkeypatch):
    origins = ("https://one.example", "https://two.example")
    monkeypatch.setattr(main.settings, "CORS_ORIGINS", origins)
    app = main.create_app()
    middleware = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    assert middleware.kwargs["allow_origins"] == list(origins)
    assert middleware.kwargs["allow_credentials"] is True


async def _post_start(client, **headers):
    return await client.post(
        "/api/interview/start",
        headers=headers,
        json={"position": "Backend"},
    )


async def test_cookie_write_without_origin_is_forbidden():
    transport = httpx.ASGITransport(app=main.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _post_start(client, Cookie="interview_session=stolen")
    assert response.status_code == 403
    assert response.json() == {"detail": "origin not allowed"}


async def test_cookie_write_with_allowlisted_origin_passes_origin_check(monkeypatch):
    monkeypatch.setattr(
        main.settings, "CORS_ORIGINS", ("http://localhost:3000",), raising=False,
    )
    transport = httpx.ASGITransport(app=main.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _post_start(
            client,
            Cookie="interview_session=stolen",
            Origin="http://localhost:3000",
        )
    assert response.status_code != 403


async def test_cookie_write_accepts_allowlisted_referer(monkeypatch):
    monkeypatch.setattr(
        main.settings, "CORS_ORIGINS", ("http://localhost:3000",), raising=False,
    )
    transport = httpx.ASGITransport(app=main.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _post_start(
            client,
            Cookie="interview_session=stolen",
            Referer="http://localhost:3000/interview/s1",
        )
    assert response.status_code != 403


async def test_cookie_write_rejects_foreign_origin(monkeypatch):
    monkeypatch.setattr(
        main.settings, "CORS_ORIGINS", ("http://localhost:3000",), raising=False,
    )
    transport = httpx.ASGITransport(app=main.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _post_start(
            client,
            Cookie="interview_session=stolen",
            Origin="https://evil.example",
        )
    assert response.status_code == 403


async def test_write_without_cookie_does_not_require_origin():
    transport = httpx.ASGITransport(app=main.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _post_start(client)
    assert response.status_code != 403


async def test_chat_callback_without_origin_is_not_blocked_by_csrf():
    transport = httpx.ASGITransport(app=main.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/chat_callback",
            json={"messages": []},
            headers={"Cookie": "interview_session=stolen"},
        )
    assert response.status_code != 403 or response.json().get("detail") != "origin not allowed"


async def test_liveness_does_not_check_redis(monkeypatch):
    async def fail_if_called():
        raise AssertionError("liveness must not check Redis")

    monkeypatch.setattr(main.health_api, "check_redis", fail_if_called)
    transport = httpx.ASGITransport(app=main.create_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


async def test_readiness_returns_generic_503_without_dependency_details(monkeypatch):
    async def unavailable():
        return False

    # 固定后端为非 postgres，使 pg/migration 组件确定性为 disabled，
    # 让本用例不依赖运行环境（CI 用 postgres，本地默认 sqlite）。
    monkeypatch.setattr(main.settings, "STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(main.settings, "CELERY_BROKER_URL", "")
    monkeypatch.setattr(main.health_api, "check_redis_readiness", unavailable)
    transport = httpx.ASGITransport(app=main.create_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "components": {
            "configuration": "ready",
            "migrations": "disabled",
            "postgresql": "disabled",
            "redis": "unavailable",
            "rabbitmq": "disabled",
        },
    }
    assert "host" not in response.text.lower()


async def test_lifespan_initializes_and_closes_resources(monkeypatch):
    events = []

    async def record(name):
        events.append(name)

    monkeypatch.setattr(main, "init_storage", lambda: record("init_storage"))
    monkeypatch.setattr(main, "init_redis", lambda: record("init_redis"))
    monkeypatch.setattr(main, "init_graph", lambda: record("init_graph"))
    monkeypatch.setattr(main, "close_graph", lambda: record("close_graph"))
    monkeypatch.setattr(main, "close_storage", lambda: record("close_storage"))
    monkeypatch.setattr(main, "close_redis", lambda: record("close_redis"))
    monkeypatch.setattr(main.registry, "render_all", lambda: None)
    monkeypatch.setattr(main.registry, "errors", [])
    monkeypatch.setattr("services.storage.get_tos_store", lambda: None)

    async with main.lifespan(main.create_app()):
        assert events == ["init_redis", "init_storage", "init_graph"]
        assert (await main.health_api.startup()).status_code == 200

    assert (await main.health_api.startup()).status_code == 503
    assert events == [
        "init_redis", "init_storage", "init_graph", "close_graph",
        "close_redis", "close_storage",
    ]


async def test_lifespan_closes_redis_when_startup_fails(monkeypatch):
    events = []

    async def record(name):
        events.append(name)

    async def fail_storage():
        raise RuntimeError("storage startup failed")

    monkeypatch.setattr(main, "init_redis", lambda: record("init_redis"))
    monkeypatch.setattr(main, "init_storage", fail_storage)
    monkeypatch.setattr(main, "close_graph", lambda: record("close_graph"))
    monkeypatch.setattr(main, "close_redis", lambda: record("close_redis"))
    monkeypatch.setattr(main, "close_storage", lambda: record("close_storage"))

    with pytest.raises(RuntimeError, match="storage startup failed"):
        async with main.lifespan(main.create_app()):
            pass

    assert events == [
        "init_redis", "close_graph", "close_redis", "close_storage",
    ]
