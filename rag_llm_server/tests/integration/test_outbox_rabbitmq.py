import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
from dotenv import load_dotenv
from kombu import Exchange, Queue
from sqlalchemy import delete, select

from config import Config
from db.engine import build_database_runtime
from db.models import AppUser, OutboxEvent
from services.jobs.dispatcher import JobDispatcher
from services.jobs.handlers import JobType
from services.jobs.outbox import (
    CeleryOutboxPublisher,
    DeliveryRoute,
    OutboxDispatcher,
    OutboxRepository,
)
from services.jobs.repository import JobRepository
from services.jobs.types import JobConflictError
from tasks.celery_app import create_celery_app


def _unavailable_broker_url(broker_url: str) -> str:
    parsed = urlsplit(broker_url)
    host = parsed.hostname or "127.0.0.1"
    username = parsed.username or ""
    password = parsed.password or ""
    credentials = ""
    if username:
        credentials = quote(username, safe="")
        if password:
            credentials += f":{quote(password, safe='')}"
        credentials += "@"
    return urlunsplit(
        (parsed.scheme, f"{credentials}{host}:1", parsed.path, "", "")
    )


async def test_rabbitmq_recovers_after_publish_failure_with_persistent_message(
    monkeypatch,
):
    load_dotenv()
    broker_url = os.getenv("CELERY_BROKER_TEST_URL")
    if not broker_url:
        pytest.skip("CELERY_BROKER_TEST_URL is required for RabbitMQ integration")
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for RabbitMQ integration")

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("CELERY_BROKER_CONNECTION_TIMEOUT", "1")
    monkeypatch.setenv("CELERY_BROKER_MAX_RETRIES", "0")
    runtime = build_database_runtime(Config())
    await runtime.start()

    suffix = uuid.uuid4().hex
    owner_id = uuid.uuid4()
    exchange = Exchange(f"deepscout.outbox.acceptance.{suffix}", durable=True)
    queue = Queue(
        f"deepscout.outbox.acceptance.{suffix}",
        exchange=exchange,
        routing_key="job",
        durable=True,
    )
    route = DeliveryRoute(
        task_name="tests.acceptance.consume_job",
        queue=queue.name,
        routing_key="job",
    )
    routes = {JobType.INTERVIEW_FINISH: route}
    try:
        async with runtime.session_scope() as session:
            session.add(
                AppUser(
                    id=owner_id,
                    username=f"outbox_rabbit_{suffix}",
                    password_hash="test-only",
                )
            )
        async with runtime.session_scope() as session:
            job = await JobDispatcher(session).enqueue(
                job_type=JobType.INTERVIEW_FINISH,
                owner_id=owner_id,
                payload_ref={
                    "schema_version": 1,
                    "session_id": str(uuid.uuid4()),
                },
                idempotency_key=f"outbox-rabbit-{suffix}",
            )
        async with runtime.session_factory() as session:
            event_record = (
                await OutboxRepository(session).claim_due(
                    now=datetime.now(timezone.utc) + timedelta(seconds=1),
                    limit=1,
                )
            )[0]
            await session.rollback()

        monkeypatch.setenv(
            "CELERY_BROKER_URL",
            _unavailable_broker_url(broker_url),
        )
        unavailable_app = create_celery_app(Config())
        unavailable_app.amqp.queues.add(queue)
        failed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        async with runtime.session_scope() as session:
            failed = await OutboxDispatcher(
                session,
                CeleryOutboxPublisher(unavailable_app, routes=routes),
            ).dispatch_batch(now=failed_at, limit=1)
        assert failed.failed == 1

        monkeypatch.setenv("CELERY_BROKER_URL", broker_url)
        available_app = create_celery_app(Config())
        available_app.amqp.queues.add(queue)
        with available_app.connection_for_write() as connection:
            bound_queue = queue(connection.channel())
            bound_queue.declare()

        async with runtime.session_scope() as session:
            recovered = await OutboxDispatcher(
                session,
                CeleryOutboxPublisher(available_app, routes=routes),
            ).dispatch_batch(now=failed_at + timedelta(seconds=6), limit=1)
        assert recovered.published == 1
        await CeleryOutboxPublisher(available_app, routes=routes).publish(event_record)

        with available_app.connection_for_read() as connection:
            channel = connection.channel()
            bound_queue = queue(channel)
            messages = [bound_queue.get(no_ack=False) for _ in range(2)]
            assert all(message is not None for message in messages)
            for message in messages:
                assert message.payload[1] == {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "job_type": JobType.INTERVIEW_FINISH.value,
                }
                assert message.properties["delivery_mode"] == 2
                assert message.properties["correlation_id"] == str(job.id)
                message.ack()

        async with runtime.session_scope() as session:
            jobs = JobRepository(session)
            await jobs.claim(job.id, lease_duration=timedelta(minutes=5))
            with pytest.raises(JobConflictError):
                await jobs.claim(job.id, lease_duration=timedelta(minutes=5))

        async with runtime.session_scope() as session:
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id)
            )
        assert event.attempt == 1
        assert event.published_at is not None
    finally:
        try:
            monkeypatch.setenv("CELERY_BROKER_URL", broker_url)
            cleanup_app = create_celery_app(Config())
            with cleanup_app.connection_for_write() as connection:
                channel = connection.channel()
                queue(channel).delete(if_unused=False, if_empty=False)
                exchange(channel).delete()
        finally:
            async with runtime.session_scope() as session:
                await session.execute(delete(AppUser).where(AppUser.id == owner_id))
            await runtime.close()
