"""Redis Lua 原子认证限流。"""
import secrets
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import NoScriptError

from services.redis_client import redis_error_boundary
from services.redis_keys import (
    CALLBACK_RATE_WINDOW_SECONDS,
    LLM_RATE_WINDOW_SECONDS,
    LOGIN_WINDOW_SECONDS,
    REGISTER_WINDOW_SECONDS,
    callback_rate_limit_key,
    llm_rate_limit_key,
    login_rate_limit_key,
    register_rate_limit_key,
)

LOGIN_MAXIMUM = 5
REGISTER_MAXIMUM = 10
EXPENSIVE_MAXIMUM = 20
CALLBACK_MAXIMUM = 30

_CONSUME_SCRIPT = """
local key = KEYS[1]
local window_ms = tonumber(ARGV[1]) * 1000
local maximum = tonumber(ARGV[2])
local member = ARGV[3]
local current = redis.call('TIME')
local now_ms = current[1] * 1000 + math.floor(current[2] / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= maximum then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = math.max(1, tonumber(oldest[2]) + window_ms - now_ms)
  redis.call('PEXPIRE', key, window_ms)
  return {0, math.ceil(retry_ms / 1000)}
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, 0}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int


class RateLimiter:
    def __init__(
        self,
        client: Redis,
        app_env: str,
        *,
        login_maximum: int = LOGIN_MAXIMUM,
        register_maximum: int = REGISTER_MAXIMUM,
        expensive_maximum: int = EXPENSIVE_MAXIMUM,
        callback_maximum: int = CALLBACK_MAXIMUM,
    ) -> None:
        self._client = client
        self._app_env = app_env
        self._login_maximum = login_maximum
        self._register_maximum = register_maximum
        self._expensive_maximum = expensive_maximum
        self._callback_maximum = callback_maximum
        self._consume_sha: str | None = None

    def matches(self, client: Redis, app_env: str) -> bool:
        return self._client is client and self._app_env == app_env

    async def _consume(self, key: str, window: int, maximum: int) -> RateLimitDecision:
        async with redis_error_boundary(self._client) as client:
            if self._consume_sha is None:
                self._consume_sha = await client.script_load(_CONSUME_SCRIPT)
            args = (window, maximum, secrets.token_hex(16))
            try:
                result = await client.evalsha(self._consume_sha, 1, key, *args)
            except NoScriptError:
                self._consume_sha = await client.script_load(_CONSUME_SCRIPT)
                result = await client.evalsha(self._consume_sha, 1, key, *args)
        return RateLimitDecision(bool(result[0]), int(result[1]))

    async def consume_login(self, client_ip: str, username: str) -> RateLimitDecision:
        key = login_rate_limit_key(self._app_env, client_ip, username)
        return await self._consume(key, LOGIN_WINDOW_SECONDS, self._login_maximum)

    async def consume_register(self, client_ip: str) -> RateLimitDecision:
        key = register_rate_limit_key(self._app_env, client_ip)
        return await self._consume(key, REGISTER_WINDOW_SECONDS, self._register_maximum)

    async def consume_expensive(self, user_id: str) -> RateLimitDecision:
        key = llm_rate_limit_key(self._app_env, user_id)
        return await self._consume(key, LLM_RATE_WINDOW_SECONDS, self._expensive_maximum)

    async def consume_callback(self, client_ip: str, callback_id: str) -> RateLimitDecision:
        key = callback_rate_limit_key(self._app_env, client_ip, callback_id)
        return await self._consume(key, CALLBACK_RATE_WINDOW_SECONDS, self._callback_maximum)

    async def clear_login(self, client_ip: str, username: str) -> None:
        key = login_rate_limit_key(self._app_env, client_ip, username)
        async with redis_error_boundary(self._client) as client:
            await client.delete(key)
