"""关键写请求的 Redis 幂等契约。"""
import asyncio
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from config import settings
from middleware.idempotency import (
    IdempotencyDecision,
    IdempotencyKeyMiddleware,
    IdempotencyStore,
    execute_idempotent,
    validate_idempotency_key,
)
from services.redis_client import SharedStateUnavailable
from services.redis_keys import idempotency_record_key


async def _redis_client():
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is required for idempotency integration")
    return Redis.from_url(url, decode_responses=True)


def _app(store, operation):
    app = FastAPI()
    app.add_middleware(IdempotencyKeyMiddleware, protected_routes={"/write"})

    @app.post("/write")
    async def write(request: Request):
        body = await request.json()
        user = {"id": request.headers.get("X-Test-User", "user-1")}
        return await execute_idempotent(
            request,
            user,
            body,
            lambda: operation(body),
            store=store,
        )

    return app


async def _request(client, key, body, user="user-1"):
    headers = {"X-Test-User": user}
    if key is not None:
        headers["Idempotency-Key"] = key
    return await client.post("/write", headers=headers, json=body)


async def test_repeated_request_replays_safe_response_and_isolates_users():
    redis = await _redis_client()
    key = f"idem-{uuid.uuid4().hex}"
    calls = 0

    async def operation(body):
        nonlocal calls
        calls += 1
        return {"call": calls, "value": body["value"]}

    store = IdempotencyStore(redis, "test")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(store, operation)),
            base_url="http://test",
        ) as client:
            first = await _request(client, key, {"value": 1})
            replay = await _request(client, key, {"value": 1})
            other_user = await _request(client, key, {"value": 1}, user="user-2")

        assert first.status_code == replay.status_code == other_user.status_code == 200
        assert first.json() == replay.json() == {"call": 1, "value": 1}
        assert other_user.json() == {"call": 2, "value": 1}
        assert first.headers["Idempotency-Replayed"] == "false"
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert calls == 2
        ttl = await redis.ttl(idempotency_record_key(
            "test", "user-1", "POST", "/write", key
        ))
        assert 0 < ttl <= 24 * 60 * 60
        raw_record = await redis.get(idempotency_record_key(
            "test", "user-1", "POST", "/write", key
        ))
        assert "owner_token" not in raw_record
    finally:
        for user_id in ("user-1", "user-2"):
            await redis.delete(idempotency_record_key(
                "test", user_id, "POST", "/write", key
            ))
        await redis.aclose()


async def test_same_key_with_different_body_returns_conflict():
    redis = await _redis_client()
    key = f"idem-{uuid.uuid4().hex}"

    async def operation(body):
        return body

    store = IdempotencyStore(redis, "test")
    record_key = idempotency_record_key("test", "user-1", "POST", "/write", key)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(store, operation)),
            base_url="http://test",
        ) as client:
            assert (await _request(client, key, {"value": 1})).status_code == 200
            conflict = await _request(client, key, {"value": 2})
        assert conflict.status_code == 409
        assert conflict.json() == {"detail": "idempotency key conflicts with request body"}
    finally:
        await redis.delete(record_key)
        await redis.aclose()


async def test_concurrent_duplicate_waits_and_replays_single_execution():
    redis = await _redis_client()
    key = f"idem-{uuid.uuid4().hex}"
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation(body):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"value": body["value"]}

    store = IdempotencyStore(redis, "test", wait_timeout=0.5, poll_interval=0.01)
    record_key = idempotency_record_key("test", "user-1", "POST", "/write", key)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(store, operation)),
            base_url="http://test",
        ) as client:
            first_task = asyncio.create_task(_request(client, key, {"value": 1}))
            await started.wait()
            assert await redis.ttl(record_key) >= 5 * 60 - 1
            second_task = asyncio.create_task(_request(client, key, {"value": 1}))
            await asyncio.sleep(0)
            release.set()
            first, second = await asyncio.gather(first_task, second_task)
        assert calls == 1
        assert first.json() == second.json() == {"value": 1}
        assert {first.headers["Idempotency-Replayed"], second.headers["Idempotency-Replayed"]} == {
            "false", "true"
        }
    finally:
        await redis.delete(record_key)
        await redis.aclose()


async def test_processing_timeout_returns_retryable_conflict():
    redis = await _redis_client()
    key = f"idem-{uuid.uuid4().hex}"
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation(_body):
        started.set()
        await release.wait()
        return {"ok": True}

    store = IdempotencyStore(redis, "test", wait_timeout=0.02, poll_interval=0.005)
    record_key = idempotency_record_key("test", "user-1", "POST", "/write", key)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(store, operation)),
            base_url="http://test",
        ) as client:
            first_task = asyncio.create_task(_request(client, key, {"value": 1}))
            await started.wait()
            processing = await _request(client, key, {"value": 1})
            release.set()
            await first_task
        assert processing.status_code == 409
        assert processing.headers["Retry-After"] == "1"
        assert processing.json() == {"detail": "idempotent request is processing"}
    finally:
        await redis.delete(record_key)
        await redis.aclose()


@pytest.mark.parametrize("key", ["x" * 15, "x" * 129])
async def test_invalid_key_is_rejected_at_http_boundary(key):
    async def operation(body):
        return body

    app = _app(None, operation)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await _request(client, key, {"value": 1})
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid Idempotency-Key"}


def test_non_ascii_key_is_rejected_before_storage():
    with pytest.raises(ValueError, match="invalid Idempotency-Key"):
        validate_idempotency_key("幂等-key-1234567890")


@pytest.mark.parametrize("key", ["x" * 16, "x" * 128])
def test_key_length_boundaries_are_accepted(key):
    assert validate_idempotency_key(key) == key


async def test_missing_key_preserves_existing_behavior():
    calls = 0

    async def operation(body):
        nonlocal calls
        calls += 1
        return body

    app = _app(None, operation)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await _request(client, None, {"value": 1})
        second = await _request(client, None, {"value": 1})
    assert first.status_code == second.status_code == 200
    assert "Idempotency-Replayed" not in first.headers
    assert calls == 2


async def test_server_error_releases_record_for_retry():
    redis = await _redis_client()
    key = f"idem-{uuid.uuid4().hex}"
    calls = 0

    async def operation(_body):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPException(status_code=503, detail="temporary failure")
        return {"ok": True}

    store = IdempotencyStore(redis, "test")
    record_key = idempotency_record_key("test", "user-1", "POST", "/write", key)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(store, operation)),
            base_url="http://test",
        ) as client:
            failed = await _request(client, key, {"value": 1})
            retried = await _request(client, key, {"value": 1})
        assert failed.status_code == 503
        assert retried.status_code == 200
        assert calls == 2
    finally:
        await redis.delete(record_key)
        await redis.aclose()


async def test_release_failure_returns_shared_state_503():
    class ReleaseFailureStore:
        async def begin(self, *_args):
            return IdempotencyDecision("acquired", owner_token="owner")

        def record_key(self, *_args):
            return "hashed-record-key"

        async def release(self, *_args):
            raise SharedStateUnavailable("secret-cache.internal refused")

    async def operation(_body):
        raise HTTPException(status_code=502, detail="provider failed")

    app = _app(ReleaseFailureStore(), operation)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await _request(client, "valid-idempotency-key", {"value": 1})
    assert response.status_code == 503
    assert response.json() == {"detail": "shared state unavailable"}
    assert "secret-cache" not in response.text


async def test_expired_record_executes_again():
    redis = await _redis_client()
    key = f"idem-{uuid.uuid4().hex}"
    calls = 0

    async def operation(_body):
        nonlocal calls
        calls += 1
        return {"call": calls}

    store = IdempotencyStore(redis, "test")
    record_key = idempotency_record_key("test", "user-1", "POST", "/write", key)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app(store, operation)),
            base_url="http://test",
        ) as client:
            assert (await _request(client, key, {"value": 1})).json() == {"call": 1}
            await redis.expire(record_key, 0)
            assert (await _request(client, key, {"value": 1})).json() == {"call": 2}
    finally:
        await redis.delete(record_key)
        await redis.aclose()


async def test_redis_error_fails_closed_without_host_leak():
    class BrokenRedis:
        async def set(self, *_args, **_kwargs):
            raise RedisConnectionError("secret-cache.internal:6379 refused")

    async def operation(body):
        return body

    app = _app(IdempotencyStore(BrokenRedis(), "test"), operation)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await _request(client, "valid-idempotency-key", {"value": 1})
    assert response.status_code == 503
    assert response.json() == {"detail": "shared state unavailable"}
    assert "secret-cache" not in response.text


def test_main_registers_idempotency_boundary():
    import main

    middleware = [item.cls for item in main.create_app().user_middleware]
    assert IdempotencyKeyMiddleware in middleware


async def test_invalid_key_response_keeps_request_and_security_headers():
    import main

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.create_app()),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/interview/start",
            headers={"Idempotency-Key": "too-short"},
            json={"position": "Backend"},
        )
    assert response.status_code == 400
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
