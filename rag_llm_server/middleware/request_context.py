"""Request ID validation, propagation and per-request context isolation."""
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger(__name__)


def _valid_request_id(value: str | None) -> bool:
    return bool(value) and len(value) <= 128 and all(32 <= ord(char) <= 126 for char in value)


def get_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def request_id_context(value: str):
    token = _request_id.set(value)
    try:
        yield
    finally:
        _request_id.reset(token)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        candidate = request.headers.get("X-Request-ID")
        request_id = candidate if _valid_request_id(candidate) else str(uuid.uuid4())
        token: Token = _request_id.set(request_id)
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                logger.error(
                    "Unhandled request error",
                    extra={"event": "request_unhandled_error", "error_code": "INTERNAL_ERROR"},
                    exc_info=exc,
                )
                response = JSONResponse(
                    status_code=500,
                    content={"detail": "服务器内部错误，请稍后重试"},
                )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _request_id.reset(token)
