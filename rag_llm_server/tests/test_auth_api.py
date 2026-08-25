import os

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

import api.auth as auth_api
from services.auth_service import token_digest
from services.rate_limit import RateLimitDecision
from services.redis_client import SharedStateUnavailable
from services.redis_keys import auth_session_key
from services.session_cache import SessionCache


@pytest.fixture(autouse=True)
def allow_auth_rate_limits(monkeypatch):
    class AllowLimiter:
        async def consume_register(self, _client_ip):
            return RateLimitDecision(True, 0)

        async def consume_login(self, _client_ip, _username):
            return RateLimitDecision(True, 0)

        async def clear_login(self, _client_ip, _username):
            return None

    monkeypatch.setattr(auth_api, "get_rate_limiter", lambda: AllowLimiter())


async def test_register_me_and_logout_cookie_flow(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    monkeypatch.setattr(auth_api, "storage", storage)
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        registered = await client.post(
            "/api/auth/register", json={"username": "Alice_01", "password": "password-123"},
        )
        assert registered.status_code == 201
        assert registered.json() == {"id": registered.json()["id"], "username": "alice_01"}
        cookie = registered.headers["set-cookie"]
        assert "interview_session=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie

        me = await client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "alice_01"

        logout = await client.post("/api/auth/logout")
        assert logout.status_code == 204
        assert (await client.get("/api/auth/me")).status_code == 401
    await storage.close()


async def test_duplicate_username_and_generic_login_error(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    monkeypatch.setattr(auth_api, "storage", storage)
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = {"username": "alice", "password": "password-123"}
        assert (await client.post("/api/auth/register", json=body)).status_code == 201
        assert (await client.post("/api/auth/register", json=body)).status_code == 409
        response = await client.post(
            "/api/auth/login", json={"username": "alice", "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid username or password"}
        malformed = await client.post(
            "/api/auth/login", json={"username": "用户", "password": "wrong-password"},
        )
        assert malformed.status_code == 401
        assert malformed.json() == {"detail": "invalid username or password"}
    await storage.close()


async def test_login_rate_limit_returns_retry_after(monkeypatch):
    class DenyLimiter:
        async def consume_login(self, _client_ip, _username):
            return RateLimitDecision(False, 42)

    monkeypatch.setattr(auth_api, "get_rate_limiter", lambda: DenyLimiter())
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login", json={"username": "missing", "password": "wrong-password"},
        )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"


async def test_session_cache_failure_returns_503_instead_of_401(monkeypatch):
    class FailingCache:
        async def resolve(self, _digest, _loader):
            raise SharedStateUnavailable("Redis shared state is unavailable")

    monkeypatch.setattr(auth_api.settings, "AUTH_SESSION_CACHE_ENABLED", True)
    monkeypatch.setattr(auth_api, "get_session_cache", lambda: FailingCache())
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(auth_api.COOKIE_NAME, "opaque-token")
        response = await client.get("/api/auth/me")

    assert response.status_code == 503
    assert response.json() == {"detail": "shared state unavailable"}


async def test_two_api_clients_share_login_and_logout_state(tmp_storage, monkeypatch):
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is required for Redis integration")
    storage = await tmp_storage()
    first_redis = Redis.from_url(url, decode_responses=True)
    second_redis = Redis.from_url(url, decode_responses=True)
    caches = {
        "active": SessionCache(first_redis, "test"),
        "first": SessionCache(first_redis, "test"),
        "second": SessionCache(second_redis, "test"),
    }
    token = None
    monkeypatch.setattr(auth_api, "storage", storage)
    monkeypatch.setattr(auth_api.settings, "APP_ENV", "test")
    monkeypatch.setattr(auth_api.settings, "AUTH_SESSION_CACHE_ENABLED", True)
    monkeypatch.setattr(auth_api, "get_session_cache", lambda: caches["active"])
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    try:
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as first,
            httpx.AsyncClient(transport=transport, base_url="http://test") as second,
        ):
            registered = await first.post(
                "/api/auth/register",
                json={"username": "shared_user", "password": "password-123"},
            )
            assert registered.status_code == 201
            token = first.cookies.get(auth_api.COOKIE_NAME)
            second.cookies.set(auth_api.COOKIE_NAME, token)

            caches["active"] = caches["second"]
            assert (await second.get("/api/auth/me")).status_code == 200
            assert (await second.post("/api/auth/logout")).status_code == 204

            caches["active"] = caches["first"]
            assert (await first.get("/api/auth/me")).status_code == 401
    finally:
        if token:
            await first_redis.delete(auth_session_key("test", token_digest(token)))
        await first_redis.aclose()
        await second_redis.aclose()
        await storage.close()
