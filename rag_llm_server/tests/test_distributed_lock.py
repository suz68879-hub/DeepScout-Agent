"""Redis 分布式租约锁的 owner 与 fencing 行为测试。"""
import asyncio
import os
import uuid

import pytest
from redis.asyncio import Redis

from services.distributed_lock import DistributedLock, LockBusy, LockLost
from services.redis_keys import rtc_fence_key, rtc_lock_key


async def clients():
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is required for Redis integration")
    return (
        Redis.from_url(url, decode_responses=True),
        Redis.from_url(url, decode_responses=True),
    )


async def cleanup(client, resource_id):
    await client.delete(rtc_lock_key("test", resource_id))
    await client.delete(rtc_fence_key("test", resource_id))


async def test_mutex_owner_checks_and_fencing_monotonicity():
    first_client, second_client = await clients()
    resource_id = uuid.uuid4().hex
    first = DistributedLock(first_client, "test", lease_ms=500, renew_interval=0.1)
    second = DistributedLock(second_client, "test", lease_ms=500, renew_interval=0.1)
    try:
        lease = await first.acquire(resource_id)
        with pytest.raises(LockBusy):
            await second.acquire(resource_id)
        assert await second.renew(resource_id, "wrong-owner") is False
        assert await second.release(resource_id, "wrong-owner") is False
        assert await first.renew(resource_id, lease.owner_token) is True
        await lease.assert_owned()
        assert await lease.release() is True

        successor = await second.acquire(resource_id)
        assert successor.fencing_token > lease.fencing_token
        assert await successor.release() is True
    finally:
        await cleanup(first_client, resource_id)
        await first_client.aclose()
        await second_client.aclose()


async def test_expired_client_lease_can_be_taken_over():
    first_client, second_client = await clients()
    resource_id = uuid.uuid4().hex
    first = DistributedLock(first_client, "test", lease_ms=80, renew_interval=0.02)
    second = DistributedLock(second_client, "test", lease_ms=500, renew_interval=0.1)
    try:
        abandoned = await first.acquire(resource_id)
        await first_client.aclose()
        successor = await second.acquire_wait(resource_id, timeout=1.0, poll_interval=0.02)
        assert successor.fencing_token > abandoned.fencing_token
        with pytest.raises(LockLost):
            await abandoned.assert_owned()
        await successor.release()
    finally:
        await cleanup(second_client, resource_id)
        await second_client.aclose()


async def test_context_manager_renews_then_releases():
    first_client, second_client = await clients()
    resource_id = uuid.uuid4().hex
    first = DistributedLock(first_client, "test", lease_ms=120, renew_interval=0.03)
    second = DistributedLock(second_client, "test", lease_ms=120, renew_interval=0.03)
    try:
        async with first.lease(resource_id) as lease:
            await asyncio.sleep(0.2)
            await lease.assert_owned()
            with pytest.raises(LockBusy):
                await second.acquire(resource_id)
        successor = await second.acquire(resource_id)
        await successor.release()
    finally:
        await cleanup(first_client, resource_id)
        await first_client.aclose()
        await second_client.aclose()
