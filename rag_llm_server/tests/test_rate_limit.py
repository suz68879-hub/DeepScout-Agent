"""Redis Lua 认证限流的原子性与双 client 一致性测试。"""
import os
import uuid

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError, NoScriptError

from services.rate_limit import RateLimiter
from services.redis_client import SharedStateUnavailable
from services.redis_keys import login_rate_limit_key


class NoScriptRedis:
    def __init__(self):
        self.loads = 0
        self.calls = 0

    async def script_load(self, _script):
        self.loads += 1
        return f"sha-{self.loads}"

    async def evalsha(self, _sha, _keys, *_args):
        self.calls += 1
        if self.calls == 1:
            raise NoScriptError("missing")
        return [1, 0]


class BrokenRedis:
    async def script_load(self, _script):
        raise RedisConnectionError("cache.internal refused")


async def test_noscript_reloads_exactly_once():
    redis = NoScriptRedis()
    limiter = RateLimiter(redis, "test")

    decision = await limiter.consume_login("203.0.113.1", "alice")

    assert decision.allowed is True
    assert redis.loads == 2
    assert redis.calls == 2


async def test_redis_failure_is_fail_closed_without_host_leak():
    limiter = RateLimiter(BrokenRedis(), "test")

    with pytest.raises(SharedStateUnavailable) as exc_info:
        await limiter.consume_register("203.0.113.1")

    assert "cache.internal" not in str(exc_info.value)


class CountingRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}

    async def script_load(self, _script):
        return "sha"

    async def evalsha(self, _sha, _num_keys, key, _window, maximum, _member):
        count = self.counts.get(key, 0)
        if count >= int(maximum):
            return [0, 12]
        self.counts[key] = count + 1
        return [1, 0]


async def test_expensive_quota_is_isolated_per_user():
    limiter = RateLimiter(CountingRedis(), "test", expensive_maximum=1)
    first = await limiter.consume_expensive("user-1")
    second = await limiter.consume_expensive("user-1")
    other = await limiter.consume_expensive("user-2")
    assert first.allowed is True
    assert second.allowed is False
    assert second.retry_after == 12
    assert other.allowed is True


async def test_two_real_clients_share_atomic_login_limit_and_clear():
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is required for Redis integration")
    identity = f"test_{uuid.uuid4().hex}"
    first_client = Redis.from_url(url, decode_responses=True)
    second_client = Redis.from_url(url, decode_responses=True)
    first = RateLimiter(first_client, "test", login_maximum=5)
    second = RateLimiter(second_client, "test", login_maximum=5)
    key = login_rate_limit_key("test", "203.0.113.10", identity)
    try:
        for _ in range(3):
            assert (await first.consume_login("203.0.113.10", identity)).allowed is True
        for _ in range(2):
            assert (await second.consume_login("203.0.113.10", identity)).allowed is True
        denied = await first.consume_login("203.0.113.10", identity)
        assert denied.allowed is False
        assert 1 <= denied.retry_after <= 15 * 60

        await second.clear_login("203.0.113.10", identity)
        assert (await first.consume_login("203.0.113.10", identity)).allowed is True
        other = await first.consume_login("203.0.113.10", identity + "_other")
        assert other.allowed is True
    finally:
        await first_client.delete(key)
        await first_client.delete(
            login_rate_limit_key("test", "203.0.113.10", identity + "_other")
        )
        await first_client.aclose()
        await second_client.aclose()
