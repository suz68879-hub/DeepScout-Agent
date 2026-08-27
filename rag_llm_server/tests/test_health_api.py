import asyncio

import httpx
import pytest
from fastapi import FastAPI

from api import health


@pytest.fixture(autouse=True)
def reset_startup_state():
    health.mark_startup_incomplete()
    yield
    health.mark_startup_incomplete()


def _app() -> FastAPI:
    application = FastAPI()
    application.include_router(health.router)
    return application


async def _get(path: str):
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.get(path)


async def test_liveness_does_not_call_external_checks(monkeypatch):
    async def forbidden():
        raise AssertionError("liveness must not check dependencies")

    monkeypatch.setattr(health, "check_postgresql", forbidden)
    monkeypatch.setattr(health, "check_redis", forbidden)
    monkeypatch.setattr(health, "check_rabbitmq", forbidden)

    response = await _get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


async def test_readiness_reports_fixed_component_states(monkeypatch):
    async def ready():
        return "ready"

    for name in (
        "check_configuration",
        "check_migrations",
        "check_postgresql",
        "check_redis",
        "check_rabbitmq",
    ):
        monkeypatch.setattr(health, name, ready)

    response = await _get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "components": {
            "configuration": "ready",
            "migrations": "ready",
            "postgresql": "ready",
            "redis": "ready",
            "rabbitmq": "ready",
        },
    }


async def test_readiness_maps_outage_and_timeout_without_details(monkeypatch):
    async def ready():
        return "ready"

    async def unavailable():
        raise OSError("postgresql://user:password@private-db")

    async def slow():
        await asyncio.sleep(1)
        return "ready"

    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(health, "check_configuration", ready)
    monkeypatch.setattr(health, "check_migrations", ready)
    monkeypatch.setattr(health, "check_postgresql", unavailable)
    monkeypatch.setattr(health, "check_redis", slow)
    monkeypatch.setattr(health, "check_rabbitmq", ready)

    response = await _get("/health/ready")

    assert response.status_code == 503
    assert response.json()["components"] == {
        "configuration": "ready",
        "migrations": "ready",
        "postgresql": "unavailable",
        "redis": "timeout",
        "rabbitmq": "ready",
    }
    assert "password" not in response.text
    assert "private-db" not in response.text


async def test_migration_lag_has_distinct_outdated_state(monkeypatch):
    async def ready():
        return "ready"

    async def outdated():
        return "outdated"

    monkeypatch.setattr(health, "check_configuration", ready)
    monkeypatch.setattr(health, "check_migrations", outdated)
    monkeypatch.setattr(health, "check_postgresql", ready)
    monkeypatch.setattr(health, "check_redis", ready)
    monkeypatch.setattr(health, "check_rabbitmq", ready)

    response = await _get("/health/ready")

    assert response.status_code == 503
    assert response.json()["components"]["migrations"] == "outdated"


async def test_rabbitmq_check_validates_connection_without_publishing(monkeypatch):
    calls = []

    async def run_in_thread(function):
        calls.append(function)

    monkeypatch.setattr(health.settings, "CELERY_BROKER_URL", "amqp://configured")
    monkeypatch.setattr(health.asyncio, "to_thread", run_in_thread)

    assert await health.check_rabbitmq() == "ready"
    assert calls == [health._check_broker_connection]


async def test_readiness_recovers_after_dependency_returns(monkeypatch):
    available = False

    async def ready():
        return "ready"

    async def redis_state():
        return "ready" if available else "unavailable"

    monkeypatch.setattr(health, "check_configuration", ready)
    monkeypatch.setattr(health, "check_migrations", ready)
    monkeypatch.setattr(health, "check_postgresql", ready)
    monkeypatch.setattr(health, "check_redis", redis_state)
    monkeypatch.setattr(health, "check_rabbitmq", ready)

    assert (await _get("/health/ready")).status_code == 503
    available = True
    assert (await _get("/health/ready")).status_code == 200


async def test_startup_distinguishes_pending_prompt_and_ready_states():
    response = await _get("/health/startup")
    assert response.status_code == 503
    assert response.json() == {
        "status": "starting",
        "components": {"connection_pool": "pending", "prompts": "pending"},
    }

    health.mark_startup_complete(prompts_ready=False)
    response = await _get("/health/startup")
    assert response.status_code == 503
    assert response.json()["components"] == {
        "connection_pool": "ready",
        "prompts": "invalid",
    }

    health.mark_startup_complete(prompts_ready=True)
    response = await _get("/health/startup")
    assert response.status_code == 200
    assert response.json()["status"] == "started"


async def test_legacy_health_maps_ready_with_deprecation_headers(monkeypatch):
    async def ready():
        return "ready"

    for name in (
        "check_configuration",
        "check_migrations",
        "check_postgresql",
        "check_redis",
        "check_rabbitmq",
    ):
        monkeypatch.setattr(health, name, ready)

    response = await _get("/health")

    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Link"] == '</health/ready>; rel="successor-version"'
