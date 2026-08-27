"""PostgreSQL auth session 的可失效 Redis 缓存。"""
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from services.redis_client import redis_error_boundary
from services.redis_keys import (
    RedisDataError,
    auth_session_key,
    decode_payload,
    encode_payload,
    session_cache_ttl,
)

_SESSION_FIELDS = {"user_id", "username"}


class SessionCache:
    def __init__(
        self,
        client: Redis,
        app_env: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._app_env = app_env
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def read(self, token_digest: str) -> dict[str, str] | None:
        key = auth_session_key(self._app_env, token_digest)
        async with redis_error_boundary(self._client) as client:
            raw = await client.get(key)
        if raw is None:
            return None
        try:
            payload = decode_payload(raw, allowed_fields=_SESSION_FIELDS)
            if set(payload) != _SESSION_FIELDS or not all(
                isinstance(payload[field], str) and payload[field]
                for field in _SESSION_FIELDS
            ):
                raise RedisDataError("Redis session payload is invalid")
        except RedisDataError:
            await self.delete(token_digest)
            return None
        return {"id": payload["user_id"], "username": payload["username"]}

    async def write(self, token_digest: str, session: dict[str, Any]) -> None:
        user = session["user"]
        expires_at = datetime.fromisoformat(session["expires_at"])
        if expires_at.tzinfo is None:
            raise RedisDataError("session expiry must include a timezone")
        remaining = (expires_at - self._clock()).total_seconds()
        ttl = session_cache_ttl(remaining)
        payload = encode_payload(
            {"user_id": user["id"], "username": user["username"]},
            allowed_fields=_SESSION_FIELDS,
        )
        key = auth_session_key(self._app_env, token_digest)
        async with redis_error_boundary(self._client) as client:
            await client.set(key, payload, ex=ttl)

    async def delete(self, token_digest: str) -> None:
        key = auth_session_key(self._app_env, token_digest)
        async with redis_error_boundary(self._client) as client:
            await client.delete(key)

    async def resolve(
        self,
        token_digest: str,
        loader: Callable[[], Awaitable[dict[str, Any] | None]],
    ) -> dict[str, str] | None:
        cached = await self.read(token_digest)
        if cached is not None:
            return cached
        session = await loader()
        if session is None:
            return None
        await self.write(token_digest, session)
        user = session["user"]
        return {"id": user["id"], "username": user["username"]}
