"""Redis 共享状态连接与生命周期测试。"""
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from services import redis_client


class FakeRedis:
    def __init__(self, *, ping_error=None):
        self.ping_error = ping_error
        self.closed = False

    async def ping(self):
        if self.ping_error:
            raise self.ping_error
        return True

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
async def reset_redis_client():
    await redis_client.close_redis()
    yield
    await redis_client.close_redis()


def redis_config(url="redis://app:secret@cache.internal:6379/0"):
    return SimpleNamespace(
        REDIS_URL=url,
        REDIS_MAX_CONNECTIONS=12,
        REDIS_SOCKET_TIMEOUT=1.25,
        REDIS_CONNECT_TIMEOUT=0.5,
    )


async def test_init_redis_pings_shared_pool_and_close_releases_it(monkeypatch):
    client = FakeRedis()
    captured = {}

    def from_url(url, **kwargs):
        captured.update(url=url, **kwargs)
        return client

    monkeypatch.setattr(redis_client.Redis, "from_url", from_url)

    initialized = await redis_client.init_redis(redis_config())

    assert initialized is client
    assert redis_client.get_redis() is client
    assert captured == {
        "url": "redis://app:secret@cache.internal:6379/0",
        "decode_responses": True,
        "max_connections": 12,
        "socket_timeout": 1.25,
        "socket_connect_timeout": 0.5,
    }

    await redis_client.close_redis()
    assert client.closed is True


async def test_init_redis_closes_partial_client_and_returns_stable_error(monkeypatch):
    client = FakeRedis(ping_error=RedisConnectionError("secret-host:6379 refused"))
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: client)

    with pytest.raises(redis_client.SharedStateUnavailable) as exc_info:
        await redis_client.init_redis(redis_config())

    assert str(exc_info.value) == "Redis shared state is unavailable"
    assert "secret-host" not in str(exc_info.value)
    assert client.closed is True


async def test_ping_redis_maps_runtime_connection_error(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: client)
    await redis_client.init_redis(redis_config())
    client.ping_error = RedisConnectionError("cache.internal unavailable")

    with pytest.raises(redis_client.SharedStateUnavailable):
        await redis_client.ping_redis()


async def test_disabled_redis_has_no_local_fallback():
    assert await redis_client.init_redis(redis_config(url=None)) is None

    with pytest.raises(redis_client.SharedStateUnavailable):
        redis_client.get_redis()
