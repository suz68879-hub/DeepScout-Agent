"""Request ID validation, propagation and per-request context isolation."""
import asyncio
import logging
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)
logger = logging.getLogger(__name__)
_propagator = TraceContextTextMapPropagator()


def _valid_request_id(value: str | None) -> bool:
    return bool(value) and len(value) <= 128 and all(32 <= ord(char) <= 126 for char in value)


def get_request_id() -> str | None:
    return _request_id.get()


def get_job_id() -> str | None:
    return _job_id.get()


def get_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


def get_span_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.span_id:016x}" if context.is_valid else None


@contextmanager
def request_id_context(value: str):
    token = _request_id.set(value)
    try:
        yield
    finally:
        _request_id.reset(token)


@contextmanager
def job_id_context(value: str):
    token = _job_id.set(value)
    try:
        yield
    finally:
        _job_id.reset(token)


def _parent_context(headers: Mapping[str, str] | None):
    carrier = headers or {}
    parent = _propagator.extract(carrier=carrier, context=Context())
    supplied = bool(carrier.get("traceparent"))
    valid = trace.get_current_span(parent).get_span_context().is_valid
    return parent if valid else Context(), supplied and not valid


@contextmanager
def job_consumer_span(
    *,
    headers: Mapping[str, str] | None,
    job_id: str,
    operation: str,
    tracer=None,
):
    parent, rejected = _parent_context(headers)
    active_tracer = tracer or trace.get_tracer(__name__)
    attributes = {
        "messaging.system": "rabbitmq",
        "messaging.operation.name": "process",
        "job.type": operation,
    }
    if isinstance(job_id, str) and len(job_id) <= 128:
        attributes["job.id"] = job_id
        locked_job_id = job_id
    else:
        locked_job_id = None
    with active_tracer.start_as_current_span(
        f"{operation} process",
        context=parent,
        kind=SpanKind.CONSUMER,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        if rejected:
            logger.warning(
                "Rejected invalid trace context",
                extra={"event": "trace_context_rejected"},
            )
        token = _job_id.set(locked_job_id)
        try:
            yield
        except asyncio.CancelledError:
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute("error.type", "cancelled")
            raise
        except Exception:
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute("error.type", "processing")
            raise
        finally:
            _job_id.reset(token)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, tracer=None):
        super().__init__(app)
        self._tracer = tracer or trace.get_tracer(__name__)

    async def dispatch(self, request: Request, call_next):
        candidate = request.headers.get("X-Request-ID")
        request_id = candidate if _valid_request_id(candidate) else str(uuid.uuid4())
        token: Token = _request_id.set(request_id)
        parent, rejected = _parent_context(request.headers)
        try:
            with self._tracer.start_as_current_span(
                f"HTTP {request.method}",
                context=parent,
                kind=SpanKind.SERVER,
                attributes={"http.request.method": request.method},
            ) as span:
                if rejected:
                    logger.warning(
                        "Rejected invalid trace context",
                        extra={"event": "trace_context_rejected"},
                    )
                try:
                    response = await call_next(request)
                except Exception as exc:
                    logger.error(
                        "Unhandled request error",
                        extra={
                            "event": "request_unhandled_error",
                            "error_code": "INTERNAL_ERROR",
                        },
                        exc_info=exc,
                    )
                    response = JSONResponse(
                        status_code=500,
                        content={"detail": "服务器内部错误，请稍后重试"},
                    )
                route = request.scope.get("route")
                route_template = getattr(route, "path", "unknown")
                span.update_name(f"{request.method} {route_template}")
                span.set_attribute("http.route", route_template)
                span.set_attribute("http.response.status_code", response.status_code)
                if response.status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                response.headers["X-Request-ID"] = request_id
                return response
        finally:
            _request_id.reset(token)
