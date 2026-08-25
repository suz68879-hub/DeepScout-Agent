"""认证 session Redis 缓存行为与双 client 一致性测试。"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from services.redis_client import SharedStateUnavailable
from services.redis_keys import auth_session_key
from services.session_cache import SessionCache


class FakeRedis:
    def __init__(self, shared=None):
        self.data = shared if shared is not None else {}
        self.ttls = {}
        self.deleted = []
        self.error = None

    async def get(self, key):
        if self.error:
            raise self.error
        return self.data.get(key)

    async def set(self, key, value, ex):
        if self.error:
            raise self.error
        self.data[key] = value
        self.ttls[key] = ex
        return True

    async def delete(self, key):
        if self.error:
            raise self.error
        self.deleted.append(key)
        return int(self.data.pop(key, None) is not None)


def session_record(expires_at, username="alice"):
    return {
        "user": {
            "id": "user-1",
            "username": username,
            "password_hash": "must-not-be-cached",
            "role": "user",
        },
        "expires_at": expires_at.isoformat(),
    }


async def test_cache_write_uses_remaining_ttl_and_minimal_payload():
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    redis = FakeRedis()
    cache = SessionCache(redis, "test", clock=lambda: now)

    await cache.write(
        "token-digest",
        session_record(now + timedelta(seconds=90)),
    )

    key = auth_session_key("test", "token-digest")
    assert redis.ttls[key] == 90
    assert "token-digest" not in key
    assert redis.data[key] == (
        '{"schema_version":1,"user_id":"user-1","username":"alice"}'
    )
    assert "password" not in redis.data[key]


async def test_cache_hit_skips_postgres_loader():
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    redis = FakeRedis()
    cache = SessionCache(redis, "test", clock=lambda: now)
    await cache.write("digest", session_record(now + timedelta(minutes=5)))
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return None

    assert await cache.resolve("digest", loader) == {"id": "user-1", "username": "alice"}
    assert calls == 0


async def test_cache_miss_loads_postgres_once_and_backfills():
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    redis = FakeRedis()
    cache = SessionCache(redis, "test", clock=lambda: now)
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return session_record(now + timedelta(minutes=5))

    assert await cache.resolve("digest", loader) == {"id": "user-1", "username": "alice"}
    assert calls == 1
    assert auth_session_key("test", "digest") in redis.data


async def test_corrupt_cache_is_deleted_then_loads_postgres_once():
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    redis = FakeRedis()
    key = auth_session_key("test", "digest")
    redis.data[key] = '{"schema_version":999}'
    cache = SessionCache(redis, "test", clock=lambda: now)
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return session_record(now + timedelta(minutes=5))

    assert await cache.resolve("digest", loader) == {"id": "user-1", "username": "alice"}
    assert calls == 1
    assert redis.deleted == [key]


async def test_redis_connection_error_is_fail_closed():
    redis = FakeRedis()
    redis.error = RedisConnectionError("cache.internal refused")
    cache = SessionCache(redis, "test")

    with pytest.raises(SharedStateUnavailable) as exc_info:
        await cache.read("digest")

    assert "cache.internal" not in str(exc_info.value)


async def test_two_real_clients_share_session_and_cleanup_owned_key():
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is required for Redis integration")
    token_digest = uuid.uuid4().hex
    first_client = Redis.from_url(url, decode_responses=True)
    second_client = Redis.from_url(url, decode_responses=True)
    first = SessionCache(first_client, "test")
    second = SessionCache(second_client, "test")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    try:
        await first.write(token_digest, session_record(expires_at))
        assert await second.read(token_digest) == {"id": "user-1", "username": "alice"}
        await second.delete(token_digest)
        assert await first.read(token_digest) is None
    finally:
        await first_client.delete(auth_session_key("test", token_digest))
        await first_client.aclose()
        await second_client.aclose()
