"""Layered liveness, readiness and startup probes with safe component states."""

import asyncio
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from config import settings
from db.engine import get_database_runtime
from services.redis_client import check_redis_readiness
from tasks.celery_app import celery_app

router = APIRouter()
CHECK_TIMEOUT_SECONDS = 1.0
_startup_pool_ready = False
_startup_prompts_ready = False


def mark_startup_complete(*, prompts_ready: bool) -> None:
    global _startup_pool_ready, _startup_prompts_ready
    _startup_pool_ready = True
    _startup_prompts_ready = prompts_ready


def mark_startup_incomplete() -> None:
    global _startup_pool_ready, _startup_prompts_ready
    _startup_pool_ready = False
    _startup_prompts_ready = False


async def check_configuration() -> str:
    if not settings.RTC_CALLBACK_SECRET:
        return "unavailable"
    return "ready"


async def check_postgresql() -> str:
    if settings.STORAGE_BACKEND != "postgres":
        return "disabled"
    await get_database_runtime().check_connection()
    return "ready"


async def check_migrations() -> str:
    if settings.STORAGE_BACKEND != "postgres":
        return "disabled"
    alembic_config = AlembicConfig(
        str(Path(__file__).resolve().parents[1] / "alembic.ini")
    )
    expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
    async with get_database_runtime().engine.connect() as connection:
        current_head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    return "ready" if current_head == expected_head else "outdated"


async def check_redis() -> str:
    return "ready" if await check_redis_readiness() else "unavailable"


def _check_broker_connection() -> None:
    with celery_app.connection_for_write(
        connect_timeout=CHECK_TIMEOUT_SECONDS,
    ) as connection:
        connection.ensure_connection(
            max_retries=0,
            timeout=CHECK_TIMEOUT_SECONDS,
        )


async def check_rabbitmq() -> str:
    if not settings.CELERY_BROKER_URL:
        return "disabled"
    await asyncio.to_thread(_check_broker_connection)
    return "ready"


async def _component_state(check) -> str:
    try:
        state = await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return "timeout"
    except Exception:
        return "unavailable"
    return state if state in {"disabled", "outdated", "ready"} else "unavailable"


async def readiness_payload() -> tuple[dict, int]:
    checks = {
        "configuration": check_configuration,
        "migrations": check_migrations,
        "postgresql": check_postgresql,
        "redis": check_redis,
        "rabbitmq": check_rabbitmq,
    }
    states = await asyncio.gather(
        *(_component_state(check) for check in checks.values())
    )
    components = dict(zip(checks, states, strict=True))
    ready = all(state in {"disabled", "ready"} for state in components.values())
    return {
        "status": "ready" if ready else "not_ready",
        "components": components,
    }, 200 if ready else 503


@router.get("/health/live", include_in_schema=False)
async def liveness():
    await asyncio.sleep(0)
    return {"status": "live"}


@router.get("/health/ready", include_in_schema=False)
async def readiness():
    payload, status_code = await readiness_payload()
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/health/startup", include_in_schema=False)
async def startup():
    components = {
        "connection_pool": "ready" if _startup_pool_ready else "pending",
        "prompts": (
            "ready"
            if _startup_prompts_ready
            else "invalid"
            if _startup_pool_ready
            else "pending"
        ),
    }
    started = all(state == "ready" for state in components.values())
    return JSONResponse(
        status_code=200 if started else 503,
        content={
            "status": "started" if started else "starting",
            "components": components,
        },
    )


@router.get("/health", include_in_schema=False)
async def legacy_health():
    payload, status_code = await readiness_payload()
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={
            "Deprecation": "true",
            "Link": '</health/ready>; rel="successor-version"',
        },
    )
