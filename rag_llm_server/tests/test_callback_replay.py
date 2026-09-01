"""RTC 回调 EventId 重放窗口。"""
from services.callback_replay import claim_callback_replay
from services.redis_keys import callback_replay_key


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None, nx=False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


async def test_claim_callback_replay_accepts_first_event_and_rejects_duplicate():
    client = FakeRedis()
    assert await claim_callback_replay("evt-1", client=client, app_env="test", ttl=600) is True
    assert await claim_callback_replay("evt-1", client=client, app_env="test", ttl=600) is False
    assert callback_replay_key("test", "evt-1") in client.values


async def test_claim_callback_replay_isolates_event_ids():
    client = FakeRedis()
    assert await claim_callback_replay("evt-a", client=client, app_env="test", ttl=600) is True
    assert await claim_callback_replay("evt-b", client=client, app_env="test", ttl=600) is True
