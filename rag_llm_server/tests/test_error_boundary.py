"""错误边界（P7，spec §5.5/§7）：chat_callback 静默错误显式化 + 全局异常处理器。"""
import asyncio
import json
import types

from agents.interviewer import ERROR_REPLY
from api.rtc import chat_callback
from main import unhandled_exception_handler


class BadJsonRequest:
    """json() 抛异常的假 Request（模拟畸形请求体）。"""

    async def json(self):
        raise ValueError("invalid json body")


def test_chat_callback_invalid_json_returns_error_text():
    r = asyncio.run(chat_callback(BadJsonRequest(), "callback-id"))
    assert r == {"text": ERROR_REPLY}  # 显式错误话术，绝不静默返回空


async def test_unhandled_exception_handler_returns_500():
    req = types.SimpleNamespace(method="POST", url=types.SimpleNamespace(path="/api/boom"))
    resp = await unhandled_exception_handler(req, ValueError("boom"))
    assert resp.status_code == 500
    assert json.loads(resp.body)["detail"] == "服务器内部错误，请稍后重试"
