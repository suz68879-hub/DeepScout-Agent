import asyncio
import logging
from dataclasses import asdict

from celery import Celery

from config import Config, settings
from db.engine import build_database_runtime
from services.jobs.outbox import CeleryOutboxPublisher, OutboxDispatcher
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def dispatch_pending_once(
    config: Config,
    app: Celery,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Dispatch one bounded batch with an invocation-owned database runtime."""
    runtime = build_database_runtime(config)
    await runtime.start()
    try:
        async with runtime.session_scope() as session:
            result = await OutboxDispatcher(
                session,
                CeleryOutboxPublisher(app),
            ).dispatch_batch(limit=limit)
    finally:
        await runtime.close()

    summary = asdict(result)
    logger.info(
        "Outbox dispatch batch completed",
        extra={"event": "outbox_dispatch_batch_completed", **summary},
    )
    return summary


@celery_app.task(
    name="tasks.outbox_dispatcher.dispatch_pending",
    ignore_result=True,
)
def dispatch_pending(limit: int = 100) -> dict[str, int]:
    return asyncio.run(dispatch_pending_once(settings, celery_app, limit=limit))
