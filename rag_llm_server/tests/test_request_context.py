"""Request ID 透传、生成、异常响应与并发隔离。"""
import asyncio
import re

import httpx
from fastapi import FastAPI

from middleware.request_context import RequestContextMiddleware, get_request_id


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/context")
    async def context():
        await asyncio.sleep(0)
        return {"request_id": get_request_id()}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    return app


async def _client(app: FastAPI):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_request_id_is_forwarded_and_returned():
    async with await _client(_app()) as client:
        response = await client.get("/context", headers={"X-Request-ID": "upstream-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "upstream-123"
    assert response.json()["request_id"] == "upstream-123"


async def test_missing_or_invalid_request_id_generates_uuid4():
    async with await _client(_app()) as client:
        missing = await client.get("/context")
        invalid = await client.get("/context", headers={"X-Request-ID": "bad\tvalue"})
    pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    assert pattern.fullmatch(missing.headers["X-Request-ID"])
    assert pattern.fullmatch(invalid.headers["X-Request-ID"])
    assert invalid.headers["X-Request-ID"] != "bad\tvalue"


async def test_exception_response_contains_request_id_without_internal_details():
    async with await _client(_app()) as client:
        response = await client.get("/boom", headers={"X-Request-ID": "error-123"})
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "error-123"
    assert response.json() == {"detail": "服务器内部错误，请稍后重试"}


async def test_concurrent_requests_keep_isolated_context():
    async with await _client(_app()) as client:
        responses = await asyncio.gather(*(
            client.get("/context", headers={"X-Request-ID": f"request-{index}"})
            for index in range(20)
        ))
    assert [response.json()["request_id"] for response in responses] == [
        f"request-{index}" for index in range(20)
    ]

