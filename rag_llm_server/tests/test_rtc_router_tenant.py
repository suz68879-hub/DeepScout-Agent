import pytest
from fastapi import HTTPException

import api.rtc as rtc_api


class FakeStorage:
    def __init__(self, session=None):
        self.session = session

    async def session_get(self, user_id, session_id):
        if self.session and self.session["user_id"] == user_id and self.session["id"] == session_id:
            return self.session
        return None


class FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body

    async def session_get_by_callback(self, callback_id):
        if self.session and self.session["rtc_callback_id"] == callback_id:
            return self.session
        return None


async def test_get_scenes_rejects_session_owned_by_another_user(monkeypatch):
    monkeypatch.setattr(rtc_api, "storage", FakeStorage())
    with pytest.raises(HTTPException) as exc_info:
        await rtc_api.get_scenes(rtc_api.SceneRequest(SessionId="session-1"), {"id": "bob"})
    assert exc_info.value.status_code == 404


async def test_callback_requires_opaque_session_locator(monkeypatch):
    monkeypatch.setattr(rtc_api.settings, "RTC_CALLBACK_SECRET", "secret")
    with pytest.raises(HTTPException) as exc_info:
        await rtc_api.chat_callback(FakeRequest({"Signature": "invalid"}), None)
    assert exc_info.value.status_code == 400
