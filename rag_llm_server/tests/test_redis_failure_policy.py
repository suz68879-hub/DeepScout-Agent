"""Redis 故障分类与 readiness 恢复策略测试。"""
import asyncio

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from services import redis_client


class SequencedRedis:
    def __init__(self, outcomes):
        self._outcomes = iter(outcomes)
        self.closed = False

    async def ping(self):
        outcome = next(self._outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def aclose(self):
        self.closed = True


def redis_config():
    return type(
        "RedisConfig",
        (),
        {
            "REDIS_URL": "redis://cache.internal:6379/0",
            "REDIS_MAX_CONNECTIONS": 5,
            "REDIS_SOCKET_TIMEOUT": 0.1,
            "REDIS_CONNECT_TIMEOUT": 0.1,
        },
    )()


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (RedisConnectionError("secret-cache.internal refused"), "connection"),
        (RedisTimeoutError("secret-cache.internal timed out"), "timeout"),
        (asyncio.TimeoutError("secret-cache.internal delayed"), "timeout"),
    ],
)
async def test_ping_classifies_transport_failures_without_exposing_host(
    monkeypatch, error, kind,
):
    client = SequencedRedis([True, error])
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: client)
    await redis_client.init_redis(redis_config())

    with pytest.raises(redis_client.SharedStateUnavailable) as exc_info:
        await redis_client.ping_redis()

    assert exc_info.value.kind.value == kind
    assert str(exc_info.value) == "Redis shared state is unavailable"
    assert "secret-cache" not in str(exc_info.value)
    await redis_client.close_redis()


def test_data_corruption_has_distinct_internal_classification_and_safe_message():
    error = redis_client.data_unavailable()

    assert error.kind is redis_client.RedisFailureKind.DATA
    assert str(error) == "Redis shared state is unavailable"
    assert "secret-cache" not in str(error)


@pytest.mark.parametrize(
    "failure",
    [
        RedisConnectionError("cache.internal refused"),
        RedisTimeoutError("cache.internal timed out"),
    ],
)
async def test_readiness_fails_after_three_failures_and_recovers_after_two_successes(
    monkeypatch, failure,
):
    client = SequencedRedis([True, failure, failure, failure, True, True])
    monkeypatch.setattr(redis_client.Redis, "from_url", lambda *_args, **_kwargs: client)
    await redis_client.init_redis(redis_config())

    assert await redis_client.check_redis_readiness() is True
    assert await redis_client.check_redis_readiness() is True
    assert await redis_client.check_redis_readiness() is False
    assert await redis_client.check_redis_readiness() is False
    assert await redis_client.check_redis_readiness() is True
    await redis_client.close_redis()


async def test_disabled_optional_redis_does_not_make_development_unready():
    config = redis_config()
    config.REDIS_URL = None

    assert await redis_client.init_redis(config) is None
    assert await redis_client.check_redis_readiness() is True
