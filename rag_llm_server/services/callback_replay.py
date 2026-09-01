"""RTC 回调 EventId/Nonce 一次性占用，阻止窗口内重放。"""
from redis.asyncio import Redis

from config import settings
from services.callback_verify import CALLBACK_MAX_SKEW_SECONDS
from services.redis_client import get_redis, redis_error_boundary
from services.redis_keys import CALLBACK_REPLAY_TTL_SECONDS, callback_replay_key


async def claim_callback_replay(
    event_id: str,
    *,
    client: Redis | None = None,
    app_env: str | None = None,
    ttl: int | None = None,
) -> bool:
    """首次占用返回 True；同一 event_id 在 TTL 内再次出现返回 False。"""
    if not event_id:
        return False
    env = app_env or settings.APP_ENV
    ttl_seconds = ttl if ttl is not None else CALLBACK_REPLAY_TTL_SECONDS
    if ttl_seconds <= 0:
        ttl_seconds = 2 * CALLBACK_MAX_SKEW_SECONDS
    key = callback_replay_key(env, event_id)
    redis_client = client if client is not None else get_redis()
    async with redis_error_boundary(redis_client) as redis:
        claimed = await redis.set(key, "1", ex=ttl_seconds, nx=True)
    return bool(claimed)
