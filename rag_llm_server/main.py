"""FastAPI 应用装配、生命周期与通用中间件。"""
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from agents.graph import close_graph, init_graph
from agents.prompts.registry import registry
from api import analytics as analytics_api
from api import auth as auth_api
from api import debug as debug_api
from api import health as health_api
from api import interview as interview_api
from api import jobs as jobs_api
from api import recording as recording_api
from api import reports as reports_api
from api import resume as resume_api
from api import rtc as rtc_api
from config import settings
from db import close_database, init_database
from logging_config import configure_logging
from middleware.request_context import RequestContextMiddleware
from middleware.idempotency import IdempotencyKeyMiddleware
from observability.telemetry import (
    TelemetryConfig,
    initialize_telemetry,
    shutdown_telemetry,
)
from observability.metrics import is_internal_metrics_client, service_metrics
from services.redis_client import close_redis, init_redis
from services.storage import close_storage, init_storage

logger = logging.getLogger(__name__)
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin:
        return origin
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = urlparse(referer)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    health_api.mark_startup_incomplete()
    initialize_telemetry(TelemetryConfig.from_environment(settings.APP_ENV))
    try:
        await init_database()
        await init_redis()
        await init_storage()
        if not settings.RTC_CALLBACK_SECRET:
            raise RuntimeError("RTC_CALLBACK_SECRET is required")
        await init_graph()

        try:
            registry.render_all()
        except Exception as exc:
            logger.error(
                "Prompt validation failed during startup error_type=%s",
                type(exc).__name__,
            )
        if registry.errors:
            logger.warning("Prompt loading warnings: %s", registry.errors)

        health_api.mark_startup_complete(prompts_ready=not registry.errors)
        yield
    finally:
        health_api.mark_startup_incomplete()
        await close_graph()
        await close_redis()
        await close_storage()
        await close_database()
        flushed = await shutdown_telemetry(timeout_seconds=5)
        if not flushed:
            logger.warning(
                "Telemetry shutdown exceeded its time limit",
                extra={"event": "telemetry_shutdown_timeout"},
            )


async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled request error method=%s path=%s error_type=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )


def create_app() -> FastAPI:
    configure_logging(settings.APP_ENV, settings.LOG_FORMAT)
    application = FastAPI(lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(IdempotencyKeyMiddleware)

    @application.middleware("http")
    async def enforce_browser_origin(request: Request, call_next):
        if request.method in _MUTATING_METHODS and request.url.path != "/api/chat_callback":
            origin = _request_origin(request)
            if origin:
                if origin not in settings.CORS_ORIGINS:
                    return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
            elif request.cookies.get(auth_api.COOKIE_NAME):
                return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    application.add_middleware(RequestContextMiddleware)

    application.add_exception_handler(Exception, unhandled_exception_handler)

    @application.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request):
        client_host = request.client.host if request.client else None
        if not is_internal_metrics_client(client_host):
            return JSONResponse(status_code=403, content={"detail": "forbidden"})
        return Response(
            generate_latest(service_metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    application.include_router(auth_api.router)
    application.include_router(health_api.router)
    application.include_router(resume_api.router)
    application.include_router(interview_api.router)
    application.include_router(jobs_api.router)
    application.include_router(reports_api.router)
    application.include_router(analytics_api.router)
    application.include_router(recording_api.router)
    application.include_router(rtc_api.router)
    if settings.ENABLE_DEBUG_ROUTES and settings.APP_ENV != "production":
        application.include_router(debug_api.router)
    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=3001, reload=False)
