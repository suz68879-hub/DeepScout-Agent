"""带 owner、自动续租与 fencing token 的 Redis 分布式锁。"""
import asyncio
import secrets
import time
from contextlib import asynccontextmanager, suppress

from redis.asyncio import Redis
from redis.exceptions import NoScriptError

from services.redis_client import redis_error_boundary
from services.redis_keys import rtc_fence_key, rtc_lock_key

_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class LockBusy(RuntimeError):
    """资源当前由其他 owner 持有。"""


class LockLost(RuntimeError):
    """当前 owner 已失去租约。"""


class _LuaScript:
    def __init__(self, source: str) -> None:
        self._source = source
        self._sha: str | None = None

    async def execute(self, client: Redis, key: str, *args):
        if self._sha is None:
            self._sha = await client.script_load(self._source)
        try:
            return await client.evalsha(self._sha, 1, key, *args)
        except NoScriptError:
            self._sha = await client.script_load(self._source)
            return await client.evalsha(self._sha, 1, key, *args)


class RedisLease:
    def __init__(
        self,
        manager: "DistributedLock",
        resource_id: str,
        owner_token: str,
        fencing_token: int,
    ) -> None:
        self._manager = manager
        self.resource_id = resource_id
        self.owner_token = owner_token
        self.fencing_token = fencing_token
        self._lost = False

    async def renew(self) -> bool:
        renewed = await self._manager.renew(self.resource_id, self.owner_token)
        self._lost = not renewed
        return renewed

    async def release(self) -> bool:
        released = await self._manager.release(self.resource_id, self.owner_token)
        self._lost = True
        return released

    async def assert_owned(self) -> None:
        if self._lost or not await self._manager.is_owned(
            self.resource_id, self.owner_token
        ):
            self._lost = True
            raise LockLost("Redis lease ownership was lost")


class DistributedLock:
    def __init__(
        self,
        client: Redis,
        app_env: str,
        *,
        lease_ms: int = 30_000,
        renew_interval: float = 10.0,
    ) -> None:
        if lease_ms <= 0 or renew_interval <= 0 or renew_interval * 1000 >= lease_ms:
            raise ValueError("renew interval must be shorter than the positive lease")
        self._client = client
        self._app_env = app_env
        self._lease_ms = lease_ms
        self._renew_interval = renew_interval
        self._renew_script = _LuaScript(_RENEW_SCRIPT)
        self._release_script = _LuaScript(_RELEASE_SCRIPT)

    async def acquire(self, resource_id: str) -> RedisLease:
        lock_key = rtc_lock_key(self._app_env, resource_id)
        owner_token = secrets.token_urlsafe(32)
        async with redis_error_boundary(self._client) as client:
            acquired = await client.set(
                lock_key,
                owner_token,
                nx=True,
                px=self._lease_ms,
            )
            if not acquired:
                raise LockBusy("Redis lease is busy")
            fencing_token = int(
                await client.incr(rtc_fence_key(self._app_env, resource_id))
            )
        return RedisLease(self, resource_id, owner_token, fencing_token)

    async def acquire_wait(
        self,
        resource_id: str,
        *,
        timeout: float,
        poll_interval: float = 0.05,
    ) -> RedisLease:
        deadline = time.monotonic() + timeout
        while True:
            try:
                return await self.acquire(resource_id)
            except LockBusy:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                await asyncio.sleep(min(poll_interval, remaining))

    async def renew(self, resource_id: str, owner_token: str) -> bool:
        key = rtc_lock_key(self._app_env, resource_id)
        async with redis_error_boundary(self._client) as client:
            result = await self._renew_script.execute(
                client, key, owner_token, self._lease_ms
            )
        return bool(result)

    async def release(self, resource_id: str, owner_token: str) -> bool:
        key = rtc_lock_key(self._app_env, resource_id)
        async with redis_error_boundary(self._client) as client:
            result = await self._release_script.execute(client, key, owner_token)
        return bool(result)

    async def is_owned(self, resource_id: str, owner_token: str) -> bool:
        key = rtc_lock_key(self._app_env, resource_id)
        async with redis_error_boundary(self._client) as client:
            return await client.get(key) == owner_token

    async def _renew_loop(self, lease: RedisLease) -> None:
        while True:
            await asyncio.sleep(self._renew_interval)
            if not await lease.renew():
                return

    @asynccontextmanager
    async def lease(self, resource_id: str):
        lease = await self.acquire(resource_id)
        async with self._hold(lease):
            yield lease

    @asynccontextmanager
    async def lease_wait(self, resource_id: str, *, timeout: float):
        lease = await self.acquire_wait(resource_id, timeout=timeout)
        async with self._hold(lease):
            yield lease

    @asynccontextmanager
    async def _hold(self, lease: RedisLease):
        renew_task = asyncio.create_task(self._renew_loop(lease))
        try:
            yield lease
            await lease.assert_owned()
        finally:
            renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await renew_task
            await lease.release()
