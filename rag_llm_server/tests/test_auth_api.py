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


INVITE_CODE = "team-invite"


def _register_payload(username: str, password: str = "password-123", **extra):
    return {"username": username, "password": password, "invite_code": INVITE_CODE, **extra}


@pytest.fixture(autouse=True)
def allow_auth_rate_limits(monkeypatch):
    class AllowLimiter:
        async def consume_register(self, _client_ip):
            return RateLimitDecision(True, 0)

        async def consume_login(self, _client_ip, _username):
            return RateLimitDecision(True, 0)

        async def consume_expensive(self, _user_id):
            return RateLimitDecision(True, 0)

        async def consume_callback(self, _client_ip, _callback_id):
            return RateLimitDecision(True, 0)

        async def clear_login(self, _client_ip, _username):
            return None

    monkeypatch.setattr(auth_api, "get_rate_limiter", lambda: AllowLimiter())
    monkeypatch.setattr(auth_api.settings, "REGISTER_INVITE_CODE", INVITE_CODE, raising=False)


async def test_register_me_and_logout_cookie_flow(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    monkeypatch.setattr(auth_api, "storage", storage)
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        registered = await client.post(
            "/api/auth/register", json=_register_payload("Alice_01"),
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
        body = _register_payload("alice")
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
                json=_register_payload("shared_user"),
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


async def test_register_without_invite_code_is_forbidden():
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "password-123"},
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "invalid invite code"}


async def test_register_with_wrong_invite_code_is_forbidden():
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/register",
            json=_register_payload("alice", invite_code="wrong-code"),
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "invalid invite code"}


async def test_register_is_closed_when_invite_code_is_unset(monkeypatch):
    monkeypatch.setattr(auth_api.settings, "REGISTER_INVITE_CODE", "", raising=False)
    app = FastAPI()
    app.include_router(auth_api.router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/register", json=_register_payload("alice"),
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "invalid invite code"}


async def test_user_quota_returns_429_without_calling_handler(monkeypatch):
    class DenyLimiter:
        async def consume_expensive(self, user_id):
            assert user_id == "u1"
            return RateLimitDecision(False, 9)

    monkeypatch.setattr(auth_api, "get_rate_limiter", lambda: DenyLimiter())
    with pytest.raises(auth_api.HTTPException) as exc_info:
        await auth_api.require_user_quota({"id": "u1", "username": "alice"})
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "9"
