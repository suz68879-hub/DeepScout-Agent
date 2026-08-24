"""FastAPI 应用装配、生命周期与通用中间件。"""
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agents.graph import close_graph, init_graph
from agents.prompts.registry import registry
from api import analytics as analytics_api
from api import auth as auth_api
from api import debug as debug_api
from api import interview as interview_api
from api import recording as recording_api
from api import reports as reports_api
from api import resume as resume_api
from api import rtc as rtc_api
from config import settings
from middleware.request_context import RequestContextMiddleware
from services.interview_service import shutdown_cold_tasks
from services.rtc_service import clear_rtc_locks
from services.storage import close_storage, init_storage

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_storage()
    try:
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

        try:
            from services.recording_service import resume_pending
            from services.storage import get_tos_store

            if get_tos_store() is not None:
                resumed = await resume_pending()
                if resumed:
                    logger.info("Resumed %s pending recording tasks", resumed)
            else:
                logger.info("Recording recovery disabled because TOS is not configured")
        except Exception as exc:
            logger.error("Recording recovery scan failed error_type=%s", type(exc).__name__)
        yield
    finally:
        await shutdown_cold_tasks()
        clear_rtc_locks()
        await close_graph()
        await close_storage()


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
    application = FastAPI(lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def enforce_browser_origin(request: Request, call_next):
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.url.path != "/api/chat_callback"
        ):
            origin = request.headers.get("origin")
            if origin and origin not in settings.CORS_ORIGINS:
                return JSONResponse(status_code=403, content={"detail": "origin not allowed"})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    application.add_middleware(RequestContextMiddleware)

    application.add_exception_handler(Exception, unhandled_exception_handler)
    application.include_router(auth_api.router)
    application.include_router(resume_api.router)
    application.include_router(interview_api.router)
    application.include_router(reports_api.router)
    application.include_router(analytics_api.router)
    application.include_router(recording_api.router)
    application.include_router(rtc_api.router)
    if settings.ENABLE_DEBUG_ROUTES:
        application.include_router(debug_api.router)
    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=3001, reload=False)
