import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from opentelemetry.trace import NoOpTracerProvider
from dotenv import load_dotenv
from sqlalchemy import delete, select

from config import Config
from db.engine import build_database_runtime
from db.models import AppUser, OutboxEvent
from services.jobs.dispatcher import JobDispatcher
from services.jobs.handlers import JobType
from services.jobs.outbox import (
    OUTBOX_ALERT_ATTEMPT,
    CeleryOutboxPublisher,
    OutboxDispatcher,
    OutboxPublishError,
    OutboxRecord,
    OutboxRepository,
    outbox_retry_delay,
)


@pytest.fixture
async def outbox_runtime(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for outbox tests")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    runtime = build_database_runtime(Config())
    await runtime.start()

    owner_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add(
            AppUser(
                id=owner_id,
                username=f"outbox_owner_{owner_id.hex}",
                password_hash="test-only",
            )
        )
    try:
        yield runtime, owner_id
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.id == owner_id))
        await runtime.close()


async def _enqueue(runtime, owner_id, key):
    async with runtime.session_scope() as session:
        job = await JobDispatcher(session).enqueue(
            job_type=JobType.INTERVIEW_FINISH,
            owner_id=owner_id,
            payload_ref={"schema_version": 1, "session_id": str(uuid.uuid4())},
            idempotency_key=key,
        )
    return job


def test_outbox_retry_delay_uses_locked_bounded_schedule():
    assert [outbox_retry_delay(attempt) for attempt in range(1, 8)] == [
        timedelta(seconds=5),
        timedelta(seconds=30),
        timedelta(minutes=2),
        timedelta(minutes=10),
        timedelta(minutes=30),
        timedelta(minutes=30),
        timedelta(minutes=30),
    ]
    with pytest.raises(ValueError, match="positive"):
        outbox_retry_delay(0)


async def test_claim_due_skips_future_and_published_events(outbox_runtime):
    runtime, owner_id = outbox_runtime
    due_job = await _enqueue(runtime, owner_id, "outbox-due")
    future_job = await _enqueue(runtime, owner_id, "outbox-future")
    published_job = await _enqueue(runtime, owner_id, "outbox-published")
    now = datetime.now(timezone.utc)

    async with runtime.session_scope() as session:
        future = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == future_job.id)
        )
        published = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == published_job.id)
        )
        future.next_attempt_at = now + timedelta(hours=1)
        published.published_at = now

    async with runtime.session_factory() as session:
        claimed = await OutboxRepository(session).claim_due(
            now=now + timedelta(seconds=1),
            limit=10,
        )
        await session.rollback()

    assert [event.aggregate_id for event in claimed] == [due_job.id]


async def test_two_claimers_skip_each_others_locked_rows(outbox_runtime):
    runtime, owner_id = outbox_runtime
    first = await _enqueue(runtime, owner_id, "outbox-concurrent-1")
    second = await _enqueue(runtime, owner_id, "outbox-concurrent-2")
    now = datetime.now(timezone.utc) + timedelta(seconds=1)

    async with runtime.session_factory() as first_session:
        first_claim = await OutboxRepository(first_session).claim_due(now=now, limit=1)
        async with runtime.session_factory() as second_session:
            second_claim = await OutboxRepository(second_session).claim_due(
                now=now,
                limit=10,
            )
            await second_session.rollback()
        await first_session.rollback()

    claimed_ids = {
        *(event.aggregate_id for event in first_claim),
        *(event.aggregate_id for event in second_claim),
    }
    assert claimed_ids == {first.id, second.id}
    assert {event.id for event in first_claim}.isdisjoint(
        event.id for event in second_claim
    )


async def test_mark_published_and_failed_update_locked_delivery_state(outbox_runtime):
    runtime, owner_id = outbox_runtime
    published_job = await _enqueue(runtime, owner_id, "outbox-mark-published")
    failed_job = await _enqueue(runtime, owner_id, "outbox-mark-failed")
    now = datetime.now(timezone.utc) + timedelta(seconds=1)

    async with runtime.session_scope() as session:
        events = await OutboxRepository(session).claim_due(now=now, limit=10)
        by_job = {event.aggregate_id: event for event in events}
        published = await OutboxRepository(session).mark_published(
            by_job[published_job.id].id,
            published_at=now,
        )
        failed = await OutboxRepository(session).mark_failed(
            by_job[failed_job.id].id,
            failed_at=now,
        )

    assert published.published_at == now
    assert failed.published_at is None
    assert failed.attempt == 1
    assert failed.next_attempt_at == now + timedelta(seconds=5)


@pytest.mark.parametrize("limit", [0, 101])
async def test_claim_due_rejects_unbounded_batches(limit):
    with pytest.raises(ValueError, match="between 1 and 100"):
        await OutboxRepository(None).claim_due(
            now=datetime.now(timezone.utc),
            limit=limit,
        )


async def test_dispatch_batch_marks_confirmed_and_failed_events(outbox_runtime):
    runtime, owner_id = outbox_runtime
    confirmed_job = await _enqueue(runtime, owner_id, "outbox-confirmed")
    failed_job = await _enqueue(runtime, owner_id, "outbox-failed")
    now = datetime.now(timezone.utc) + timedelta(seconds=1)

    class Publisher:
        async def publish(self, event):
            if event.aggregate_id == failed_job.id:
                raise RuntimeError("password=must-not-enter-logs")

    async with runtime.session_scope() as session:
        result = await OutboxDispatcher(session, Publisher()).dispatch_batch(
            now=now,
            limit=10,
        )

    assert result.claimed == 2
    assert result.published == 1
    assert result.failed == 1
    assert result.alerting == 0
    async with runtime.session_scope() as session:
        events = (
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id.in_([confirmed_job.id, failed_job.id])
                )
            )
        ).all()
    by_job = {event.aggregate_id: event for event in events}
    assert by_job[confirmed_job.id].published_at == now
    assert by_job[failed_job.id].published_at is None
    assert by_job[failed_job.id].attempt == 1
    assert by_job[failed_job.id].next_attempt_at == now + timedelta(seconds=5)


async def test_dispatch_batch_logs_threshold_without_payload_or_exception(
    outbox_runtime,
    caplog,
):
    runtime, owner_id = outbox_runtime
    job = await _enqueue(runtime, owner_id, "outbox-alert-threshold")
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    async with runtime.session_scope() as session:
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id)
        )
        event.attempt = OUTBOX_ALERT_ATTEMPT - 1

    class Publisher:
        async def publish(self, event):
            del event
            raise RuntimeError("token=must-not-enter-logs")

    async with runtime.session_scope() as session:
        result = await OutboxDispatcher(session, Publisher()).dispatch_batch(
            now=now,
            limit=1,
        )

    assert result.alerting == 1
    assert "outbox_publish_alert" in [
        getattr(record, "event", None) for record in caplog.records
    ]
    assert "must-not-enter-logs" not in caplog.text


async def test_dispatch_batch_stops_after_broker_failure(outbox_runtime):
    runtime, owner_id = outbox_runtime
    jobs = [
        await _enqueue(runtime, owner_id, f"outbox-outage-{index}")
        for index in range(3)
    ]
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    calls = []

    class Publisher:
        async def publish(self, event):
            calls.append(event.id)
            raise ConnectionError("broker unavailable")

    async with runtime.session_scope() as session:
        result = await OutboxDispatcher(session, Publisher()).dispatch_batch(
            now=now,
            limit=3,
        )

    assert result.claimed == 3
    assert result.failed == 1
    assert result.published == 0
    assert len(calls) == 1
    async with runtime.session_scope() as session:
        events = (
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id.in_([job.id for job in jobs])
                )
            )
        ).all()
    assert sorted(event.attempt for event in events) == [0, 0, 1]


async def test_confirmed_event_is_reclaimable_when_database_mark_rolls_back(
    outbox_runtime,
    monkeypatch,
):
    runtime, owner_id = outbox_runtime
    job = await _enqueue(runtime, owner_id, "outbox-confirm-before-db-failure")
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    confirmations = []

    class Publisher:
        async def publish(self, event):
            confirmations.append(event.id)

    async def fail_mark(self, event_id, *, published_at=None):
        del self, event_id, published_at
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(OutboxRepository, "mark_published", fail_mark)
    with pytest.raises(RuntimeError, match="database unavailable"):
        async with runtime.session_scope() as session:
            await OutboxDispatcher(session, Publisher()).dispatch_batch(
                now=now,
                limit=1,
            )

    assert len(confirmations) == 1
    async with runtime.session_factory() as session:
        reclaimed = await OutboxRepository(session).claim_due(now=now, limit=1)
        await session.rollback()
    assert [event.aggregate_id for event in reclaimed] == [job.id]


async def test_celery_publisher_routes_allowlisted_job_message():
    calls = []

    class FakeApp:
        def send_task(self, name, **options):
            calls.append((name, options))

    job_id = uuid.uuid4()
    event = OutboxRecord(
        id=uuid.uuid4(),
        aggregate_type="background_job",
        aggregate_id=job_id,
        event_type="job.created",
        payload={
            "schema_version": 1,
            "job_id": str(job_id),
            "job_type": JobType.INTERVIEW_FINISH.value,
        },
        published_at=None,
        attempt=0,
        next_attempt_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )

    tracer = NoOpTracerProvider().get_tracer("test")
    await CeleryOutboxPublisher(FakeApp(), tracer=tracer).publish(event)

    assert calls == [
        (
            "tasks.interview_tasks.finish_interview",
            {
                "kwargs": event.payload,
                "task_id": str(job_id),
                "queue": "deepscout.cold",
                "routing_key": "cold",
                "serializer": "json",
                "delivery_mode": 2,
                "mandatory": True,
                "retry": True,
            },
        )
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"event_type": "job.deleted"},
        {"aggregate_type": "other"},
        {"payload": {"schema_version": 1, "job_id": "invalid"}},
        {
            "payload": {
                "schema_version": 1,
                "job_id": str(uuid.uuid4()),
                "job_type": "unknown.job",
            }
        },
    ],
)
async def test_celery_publisher_rejects_non_allowlisted_messages(changes):
    job_id = uuid.uuid4()
    values = {
        "id": uuid.uuid4(),
        "aggregate_type": "background_job",
        "aggregate_id": job_id,
        "event_type": "job.created",
        "payload": {
            "schema_version": 1,
            "job_id": str(job_id),
            "job_type": JobType.RECORDING_PROCESS.value,
        },
        "published_at": None,
        "attempt": 0,
        "next_attempt_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
    }
    values.update(changes)

    with pytest.raises(OutboxPublishError, match="INVALID_OUTBOX_MESSAGE"):
        await CeleryOutboxPublisher(SimpleNamespace()).publish(OutboxRecord(**values))


async def test_dispatch_pending_once_owns_database_lifecycle(outbox_runtime):
    from tasks.outbox_dispatcher import dispatch_pending_once

    runtime, owner_id = outbox_runtime
    job = await _enqueue(runtime, owner_id, "outbox-task-lifecycle")
    calls = []

    class FakeApp:
        def send_task(self, name, **options):
            calls.append((name, options))

    result = await dispatch_pending_once(Config(), FakeApp(), limit=1)

    assert result == {"claimed": 1, "published": 1, "failed": 0, "alerting": 0}
    assert calls[0][1]["task_id"] == str(job.id)
    async with runtime.session_scope() as session:
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id)
        )
    assert event.published_at is not None


def test_outbox_dispatch_task_is_registered_and_scheduled():
    import tasks.outbox_dispatcher  # noqa: F401
    from tasks.celery_app import celery_app

    task_name = "tasks.outbox_dispatcher.dispatch_pending"
    assert task_name in celery_app.tasks
    schedule = celery_app.conf.beat_schedule["dispatch-pending-outbox"]
    assert schedule == {
        "task": task_name,
        "schedule": 1.0,
        "options": {"queue": "deepscout.outbox", "routing_key": "outbox"},
    }
