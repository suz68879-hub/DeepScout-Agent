"""RTC 启动在两个 Redis client 间保持单次供应商调用。"""
import asyncio
import os
import uuid

import pytest
from redis.asyncio import Redis

import services.rtc_service as rtc_service
from services.distributed_lock import DistributedLock
from services.redis_keys import rtc_fence_key, rtc_lock_key


class SharedSessionStorage:
    def __init__(self, session):
        self.session = session
        self.guard = asyncio.Lock()

    async def session_get(self, user_id, session_id):
        if user_id != self.session["user_id"] or session_id != self.session["id"]:
            return None
        return dict(self.session)

    async def session_claim_rtc_fence(self, user_id, session_id, token):
        async with self.guard:
            current = await self.session_get(user_id, session_id)
            if current is None:
                return None
            if self.session["rtc_fencing_token"] >= token:
                raise AssertionError("fencing token must increase")
            self.session["rtc_fencing_token"] = token
            return dict(self.session)

    async def session_update_rtc_status(self, user_id, session_id, status, token):
        async with self.guard:
            current = await self.session_get(user_id, session_id)
            if current is None:
                return None
            if self.session["rtc_fencing_token"] != token:
                raise AssertionError("stale fencing token")
            self.session["rtc_status"] = status
            return dict(self.session)


async def test_two_api_instances_start_one_provider_agent(monkeypatch):
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is required for Redis integration")
    resource_id = uuid.uuid4().hex
    first_client = Redis.from_url(url, decode_responses=True)
    second_client = Redis.from_url(url, decode_responses=True)
    storage = SharedSessionStorage({
        "id": resource_id,
        "user_id": "owner-1",
        "rtc_status": "created",
        "rtc_fencing_token": 0,
    })
    managers = iter((
        DistributedLock(first_client, "test", lease_ms=500, renew_interval=0.1),
        DistributedLock(second_client, "test", lease_ms=500, renew_interval=0.1),
    ))
    provider_calls = 0

    async def provider(_action, _version, _session, _incoming, lease):
        nonlocal provider_calls
        await lease.assert_owned()
        provider_calls += 1
        await asyncio.sleep(0.05)
        return {"ResponseMetadata": {}}

    monkeypatch.setattr(rtc_service, "storage", storage)
    monkeypatch.setattr(rtc_service, "get_rtc_lock", lambda: next(managers))
    monkeypatch.setattr(rtc_service, "_call_provider", provider)
    try:
        results = await asyncio.gather(*(
            rtc_service.call_voice_chat_openapi(
                "StartVoiceChat", "2024-12-01", dict(storage.session), {}
            )
            for _ in range(2)
        ))
        assert provider_calls == 1
        assert storage.session["rtc_status"] == "running"
        assert any(result.get("Result", {}).get("Idempotent") for result in results)
    finally:
        await first_client.delete(rtc_lock_key("test", resource_id))
        await first_client.delete(rtc_fence_key("test", resource_id))
        await first_client.aclose()
        await second_client.aclose()
