import asyncio
import logging
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from celery import Celery
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OutboxEvent
from services.jobs.handlers import JobType

logger = logging.getLogger(__name__)

OUTBOX_ALERT_ATTEMPT = 10

_RETRY_DELAYS = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
)


class OutboxConflictError(Exception):
    """The event is no longer eligible for the requested delivery update."""


class OutboxPublishError(Exception):
    """A stable error raised before an unsafe message reaches the broker."""


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    event_type: str
    payload: dict
    published_at: datetime | None
    attempt: int
    next_attempt_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DispatchBatchResult:
    claimed: int
    published: int
    failed: int
    alerting: int


@dataclass(frozen=True, slots=True)
class DeliveryRoute:
    task_name: str
    queue: str
    routing_key: str


class OutboxPublisher(Protocol):
    def publish(self, event: OutboxRecord) -> Awaitable[None]: ...


def outbox_retry_delay(attempt: int) -> timedelta:
    if attempt < 1:
        raise ValueError("attempt must be positive")
    return _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS)) - 1]


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return current


def _record(model: OutboxEvent) -> OutboxRecord:
    return OutboxRecord(
        id=model.id,
        aggregate_type=model.aggregate_type,
        aggregate_id=model.aggregate_id,
        event_type=model.event_type,
        payload=dict(model.payload),
        published_at=model.published_at,
        attempt=model.attempt,
        next_attempt_at=model.next_attempt_at,
        created_at=model.created_at,
    )


class OutboxRepository:
    """Outbox persistence bound to one caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[OutboxRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        claimed_at = _now(now)
        models = (
            await self._session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.next_attempt_at <= claimed_at,
                )
                .order_by(
                    OutboxEvent.next_attempt_at,
                    OutboxEvent.created_at,
                    OutboxEvent.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        return [_record(model) for model in models]

    async def mark_published(
        self,
        event_id: uuid.UUID | str,
        *,
        published_at: datetime | None = None,
    ) -> OutboxRecord:
        model = await self._session.scalar(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == uuid.UUID(str(event_id)),
                OutboxEvent.published_at.is_(None),
            )
            .values(published_at=_now(published_at))
            .returning(OutboxEvent)
        )
        if model is None:
            raise OutboxConflictError("outbox event state conflict")
        return _record(model)

    async def mark_failed(
        self,
        event_id: uuid.UUID | str,
        *,
        failed_at: datetime | None = None,
    ) -> OutboxRecord:
        failed = _now(failed_at)
        model = await self._session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.id == uuid.UUID(str(event_id)),
                OutboxEvent.published_at.is_(None),
            )
            .with_for_update()
        )
        if model is None:
            raise OutboxConflictError("outbox event state conflict")
        model.attempt += 1
        model.next_attempt_at = failed + outbox_retry_delay(model.attempt)
        await self._session.flush()
        return _record(model)


_DELIVERY_ROUTES = {
    JobType.INTERVIEW_FINISH: DeliveryRoute(
        task_name="tasks.interview_tasks.finish_interview",
        queue="deepscout.cold",
        routing_key="cold",
    ),
    JobType.RECORDING_PROCESS: DeliveryRoute(
        task_name="tasks.recording_tasks.process_recording",
        queue="deepscout.recording",
        routing_key="recording",
    ),
}


def _delivery_message(event: OutboxRecord) -> tuple[JobType, dict]:
    payload = event.payload
    try:
        job_id = uuid.UUID(payload["job_id"])
        job_type = JobType(payload["job_type"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise OutboxPublishError("INVALID_OUTBOX_MESSAGE") from None
    if (
        event.aggregate_type != "background_job"
        or event.event_type != "job.created"
        or event.aggregate_id != job_id
        or frozenset(payload) != {"schema_version", "job_id", "job_type"}
        or payload.get("schema_version") != 1
    ):
        raise OutboxPublishError("INVALID_OUTBOX_MESSAGE")
    return job_type, dict(payload)


class CeleryOutboxPublisher:
    """Publish an allowlisted persistent Celery message with broker confirms."""

    def __init__(
        self,
        app: Celery,
        *,
        routes: Mapping[JobType, DeliveryRoute] | None = None,
    ) -> None:
        self._app = app
        self._routes = dict(_DELIVERY_ROUTES if routes is None else routes)

    async def publish(self, event: OutboxRecord) -> None:
        job_type, payload = _delivery_message(event)
        try:
            route = self._routes[job_type]
        except KeyError:
            raise OutboxPublishError("INVALID_OUTBOX_MESSAGE") from None
        await asyncio.to_thread(
            self._app.send_task,
            route.task_name,
            kwargs=payload,
            task_id=str(event.aggregate_id),
            queue=route.queue,
            routing_key=route.routing_key,
            serializer="json",
            delivery_mode=2,
            mandatory=True,
            retry=True,
        )


class OutboxDispatcher:
    """Publish one locked batch and persist confirm/failure outcomes atomically."""

    def __init__(self, session: AsyncSession, publisher: OutboxPublisher) -> None:
        self._outbox = OutboxRepository(session)
        self._publisher = publisher

    async def dispatch_batch(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> DispatchBatchResult:
        dispatched_at = _now(now)
        events = await self._outbox.claim_due(now=dispatched_at, limit=limit)
        published = 0
        failed = 0
        alerting = 0
        for event in events:
            try:
                await self._publisher.publish(event)
            except Exception as exc:
                failed_event = await self._outbox.mark_failed(
                    event.id,
                    failed_at=dispatched_at,
                )
                failed += 1
                is_alerting = failed_event.attempt >= OUTBOX_ALERT_ATTEMPT
                alerting += int(is_alerting)
                logger.log(
                    logging.ERROR if is_alerting else logging.WARNING,
                    "Outbox event publish failed",
                    extra={
                        "event": (
                            "outbox_publish_alert"
                            if is_alerting
                            else "outbox_publish_failed"
                        ),
                        "error_code": "OUTBOX_PUBLISH_FAILED",
                        "error_type": type(exc).__name__,
                        "outbox_event_id": str(event.id),
                        "job_id": str(event.aggregate_id),
                        "attempt": failed_event.attempt,
                        "outbox_age_seconds": max(
                            0.0,
                            (dispatched_at - event.created_at).total_seconds(),
                        ),
                    },
                )
                if not isinstance(exc, OutboxPublishError):
                    break
                continue

            await self._outbox.mark_published(
                event.id,
                published_at=dispatched_at,
            )
            published += 1
            logger.info(
                "Outbox event published",
                extra={
                    "event": "outbox_publish_succeeded",
                    "outbox_event_id": str(event.id),
                    "job_id": str(event.aggregate_id),
                    "attempt": event.attempt,
                    "outbox_age_seconds": max(
                        0.0,
                        (dispatched_at - event.created_at).total_seconds(),
                    ),
                },
            )
        return DispatchBatchResult(
            claimed=len(events),
            published=published,
            failed=failed,
            alerting=alerting,
        )
