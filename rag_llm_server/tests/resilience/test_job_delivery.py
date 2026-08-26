import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
from dotenv import load_dotenv
from kombu import Exchange, Queue
from sqlalchemy import delete, func, select

from agents.graph import ColdPathOutputError
from config import Config
from db.engine import build_database_runtime
from db.models import (
    AppUser,
    BackgroundJob,
    InterviewReport,
    InterviewSession,
    OutboxEvent,
)
from services.jobs.dispatcher import JobDispatcher
from services.jobs.handlers import JobType
from services.jobs.outbox import (
    CeleryOutboxPublisher,
    DeliveryRoute,
    OutboxDispatcher,
    OutboxRepository,
)
from services.jobs.repository import JobRepository
from services.jobs.types import JobErrorCode, JobStatus
from tasks.celery_app import create_celery_app
from tasks.interview_tasks import InterviewTaskPending, execute_interview_job
from tasks.retry_policy import DEAD_LETTER_EXCHANGE, DeadLetterPublisher


INJECTION_REPETITIONS = 10


@pytest.fixture
async def resilience_runtime(monkeypatch):
    load_dotenv()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    config = Config()
    if not config.DATABASE_URL:
        pytest.skip("DATABASE_URL is required for resilience acceptance")
    database_name = urlsplit(config.DATABASE_URL).path.strip("/")
    if not database_name.casefold().endswith("_test"):
        pytest.fail("resilience acceptance requires an isolated *_test database")
    runtime = build_database_runtime(config)
    await runtime.start()
    owner_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add(
            AppUser(
                id=owner_id,
                username=f"resilience_{owner_id.hex}",
                password_hash="test-only",
            )
        )
    try:
        yield runtime, owner_id
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.id == owner_id))
        await runtime.close()


async def _create_session(runtime, owner_id, label):
    session_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add(
            InterviewSession(
                id=session_id,
                user_id=owner_id,
                position="Resilience acceptance",
                stage="finish",
                status="finished",
                rtc_room_id=f"room-{label}-{session_id}",
                rtc_user_id=f"user-{label}-{session_id}",
                rtc_task_id=f"task-{label}-{session_id}",
                rtc_callback_id=f"callback-{label}-{session_id}",
            )
        )
    return session_id


async def _enqueue(runtime, owner_id, session_id, key):
    async with runtime.session_scope() as session:
        return await JobDispatcher(session).enqueue(
            job_type=JobType.INTERVIEW_FINISH,
            owner_id=owner_id,
            payload_ref={
                "schema_version": 1,
                "session_id": str(session_id),
                "step": "finish",
            },
            idempotency_key=key,
        )


def _report_runner(runtime, owner_id, session_id):
    async def run(_job):
        async with runtime.session_scope() as session:
            report = await session.scalar(
                select(InterviewReport).where(
                    InterviewReport.session_id == session_id,
                    InterviewReport.user_id == owner_id,
                )
            )
            if report is None:
                report = InterviewReport(
                    id=uuid.uuid4(),
                    user_id=owner_id,
                    session_id=session_id,
                    scores_json={},
                    feedback_json={},
                    suggestions_json=[],
                    position="Resilience acceptance",
                    source="session",
                )
                session.add(report)
                await session.flush()
            report_id = report.id
        return {
            "schema_version": 1,
            "session_id": str(session_id),
            "report_id": str(report_id),
        }

    return run


async def _execute(runtime, job, runner):
    return await execute_interview_job(
        runtime,
        schema_version=1,
        job_id=str(job.id),
        job_type=JobType.INTERVIEW_FINISH.value,
        cold_runner=runner,
    )


async def _assert_succeeded_once(runtime, job_id, session_id):
    async with runtime.session_scope() as session:
        job = await JobRepository(session).get_internal(job_id)
        report_count = await session.scalar(
            select(func.count())
            .select_from(InterviewReport)
            .where(InterviewReport.session_id == session_id)
        )
    assert job.status is JobStatus.SUCCEEDED
    assert report_count == 1


def _unavailable_broker_url(broker_url):
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


async def test_transaction_rollback_before_commit_leaves_no_job_or_outbox(
    resilience_runtime,
):
    runtime, owner_id = resilience_runtime
    rolled_back_job_ids = []
    for iteration in range(INJECTION_REPETITIONS):
        session_id = await _create_session(
            runtime, owner_id, f"before-commit-{iteration}"
        )
        async with runtime.session_factory() as session:
            job = await JobDispatcher(session).enqueue(
                job_type=JobType.INTERVIEW_FINISH,
                owner_id=owner_id,
                payload_ref={
                    "schema_version": 1,
                    "session_id": str(session_id),
                },
                idempotency_key=f"before-commit-{iteration}",
            )
            rolled_back_job_ids.append(job.id)
            await session.rollback()

    async with runtime.session_scope() as session:
        job_count = await session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.id.in_(rolled_back_job_ids))
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id.in_(rolled_back_job_ids))
        )
    assert (job_count, outbox_count) == (0, 0)


async def test_dispatcher_restart_publishes_committed_outbox_and_job_succeeds(
    resilience_runtime,
):
    runtime, owner_id = resilience_runtime
    published = []

    class Publisher:
        async def publish(self, event):
            published.append(event.aggregate_id)

    for iteration in range(INJECTION_REPETITIONS):
        session_id = await _create_session(
            runtime, owner_id, f"after-commit-{iteration}"
        )
        job = await _enqueue(
            runtime, owner_id, session_id, f"after-commit-{iteration}"
        )
        async with runtime.session_scope() as session:
            result = await OutboxDispatcher(session, Publisher()).dispatch_batch(
                now=datetime.now(timezone.utc) + timedelta(seconds=1),
                limit=1,
            )
        assert (result.published, result.failed) == (1, 0)
        await _execute(
            runtime,
            job,
            _report_runner(runtime, owner_id, session_id),
        )
        await _assert_succeeded_once(runtime, job.id, session_id)

    assert len(published) == INJECTION_REPETITIONS


async def test_confirm_before_mark_crash_republishes_without_duplicate_result(
    resilience_runtime,
    monkeypatch,
):
    runtime, owner_id = resilience_runtime
    publish_counts = {}

    class Publisher:
        async def publish(self, event):
            publish_counts[event.aggregate_id] = (
                publish_counts.get(event.aggregate_id, 0) + 1
            )

    for iteration in range(INJECTION_REPETITIONS):
        session_id = await _create_session(
            runtime, owner_id, f"confirm-crash-{iteration}"
        )
        job = await _enqueue(
            runtime, owner_id, session_id, f"confirm-crash-{iteration}"
        )

        async def crash_after_confirm(self, event_id, *, published_at=None):
            del self, event_id, published_at
            raise RuntimeError("injected dispatcher crash")

        with monkeypatch.context() as patcher:
            patcher.setattr(OutboxRepository, "mark_published", crash_after_confirm)
            with pytest.raises(RuntimeError, match="injected dispatcher crash"):
                async with runtime.session_scope() as session:
                    await OutboxDispatcher(session, Publisher()).dispatch_batch(
                        now=datetime.now(timezone.utc) + timedelta(seconds=1),
                        limit=1,
                    )

        async with runtime.session_scope() as session:
            result = await OutboxDispatcher(session, Publisher()).dispatch_batch(
                now=datetime.now(timezone.utc) + timedelta(seconds=1),
                limit=1,
            )
        assert result.published == 1
        runner = _report_runner(runtime, owner_id, session_id)
        first = await _execute(runtime, job, runner)
        repeated = await _execute(runtime, job, runner)
        assert first == repeated
        await _assert_succeeded_once(runtime, job.id, session_id)

    assert set(publish_counts.values()) == {2}


async def test_worker_kill_after_business_commit_recovers_expired_lease(
    resilience_runtime,
):
    runtime, owner_id = resilience_runtime
    for iteration in range(INJECTION_REPETITIONS):
        session_id = await _create_session(
            runtime, owner_id, f"worker-kill-{iteration}"
        )
        job = await _enqueue(
            runtime, owner_id, session_id, f"worker-kill-{iteration}"
        )
        expired_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        async with runtime.session_scope() as session:
            await JobRepository(session).claim(
                job.id,
                lease_duration=timedelta(seconds=1),
                now=expired_at,
            )
            session.add(
                InterviewReport(
                    id=uuid.uuid4(),
                    user_id=owner_id,
                    session_id=session_id,
                    scores_json={},
                    feedback_json={},
                    suggestions_json=[],
                    position="Resilience acceptance",
                    source="session",
                )
            )

        runner = _report_runner(runtime, owner_id, session_id)
        with pytest.raises(InterviewTaskPending):
            await _execute(runtime, job, runner)
        await _execute(runtime, job, runner)
        await _assert_succeeded_once(runtime, job.id, session_id)


async def test_duplicate_worker_delivery_keeps_business_effect_at_one(
    resilience_runtime,
):
    runtime, owner_id = resilience_runtime
    for iteration in range(INJECTION_REPETITIONS):
        session_id = await _create_session(
            runtime, owner_id, f"duplicate-{iteration}"
        )
        job = await _enqueue(runtime, owner_id, session_id, f"duplicate-{iteration}")
        runner = _report_runner(runtime, owner_id, session_id)
        first = await _execute(runtime, job, runner)
        repeated = await _execute(runtime, job, runner)
        assert first == repeated
        await _assert_succeeded_once(runtime, job.id, session_id)


async def test_rabbitmq_short_outage_recovers_all_persistent_deliveries(
    resilience_runtime,
    monkeypatch,
):
    runtime, owner_id = resilience_runtime
    broker_url = os.getenv("CELERY_BROKER_TEST_URL")
    if not broker_url:
        pytest.skip("CELERY_BROKER_TEST_URL is required for resilience acceptance")
    monkeypatch.setenv("CELERY_BROKER_CONNECTION_TIMEOUT", "1")
    monkeypatch.setenv("CELERY_BROKER_MAX_RETRIES", "0")
    suffix = uuid.uuid4().hex
    exchange = Exchange(f"deepscout.resilience.{suffix}", durable=True)
    queue = Queue(
        f"deepscout.resilience.{suffix}",
        exchange=exchange,
        routing_key="job",
        durable=True,
    )
    route = DeliveryRoute(
        task_name="tests.resilience.execute_interview",
        queue=queue.name,
        routing_key="job",
    )
    routes = {JobType.INTERVIEW_FINISH: route}
    jobs = []
    for iteration in range(INJECTION_REPETITIONS):
        session_id = await _create_session(
            runtime, owner_id, f"broker-outage-{iteration}"
        )
        jobs.append(
            (
                await _enqueue(
                    runtime,
                    owner_id,
                    session_id,
                    f"broker-outage-{iteration}",
                ),
                session_id,
            )
        )

    failed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    monkeypatch.setenv("CELERY_BROKER_URL", _unavailable_broker_url(broker_url))
    unavailable_app = create_celery_app(Config())
    unavailable_app.amqp.queues.add(queue)
    try:
        for _ in range(INJECTION_REPETITIONS):
            async with runtime.session_scope() as session:
                result = await OutboxDispatcher(
                    session,
                    CeleryOutboxPublisher(unavailable_app, routes=routes),
                ).dispatch_batch(now=failed_at, limit=1)
            assert (result.published, result.failed) == (0, 1)

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
            ).dispatch_batch(
                now=failed_at + timedelta(seconds=6),
                limit=INJECTION_REPETITIONS,
            )
        assert (recovered.published, recovered.failed) == (
            INJECTION_REPETITIONS,
            0,
        )

        jobs_by_id = {str(job.id): (job, session_id) for job, session_id in jobs}
        with available_app.connection_for_read() as connection:
            bound_queue = queue(connection.channel())
            messages = [
                bound_queue.get(no_ack=False)
                for _ in range(INJECTION_REPETITIONS)
            ]
            assert all(message is not None for message in messages)
            for message in messages:
                payload = message.payload[1]
                job, session_id = jobs_by_id[payload["job_id"]]
                await _execute(
                    runtime,
                    job,
                    _report_runner(runtime, owner_id, session_id),
                )
                message.ack()

        for job, session_id in jobs:
            await _assert_succeeded_once(runtime, job.id, session_id)
            async with runtime.session_scope() as session:
                event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == job.id
                    )
                )
            assert event.attempt == 1
            assert event.published_at is not None
    finally:
        monkeypatch.setenv("CELERY_BROKER_URL", broker_url)
        cleanup_app = create_celery_app(Config())
        with cleanup_app.connection_for_write() as connection:
            channel = connection.channel()
            queue(channel).delete(if_unused=False, if_empty=False)
            exchange(channel).delete()


async def test_terminal_failures_reach_explicit_dlq_without_business_result(
    resilience_runtime,
    monkeypatch,
):
    runtime, owner_id = resilience_runtime
    broker_url = os.getenv("CELERY_BROKER_TEST_URL")
    if not broker_url:
        pytest.skip("CELERY_BROKER_TEST_URL is required for resilience acceptance")
    monkeypatch.setenv("CELERY_BROKER_URL", broker_url)
    app = create_celery_app(Config())
    exchange = Exchange(DEAD_LETTER_EXCHANGE, type="direct", durable=True)
    observer = Queue(
        f"deepscout.dlq.resilience.{uuid.uuid4().hex}",
        exchange=exchange,
        routing_key="failed",
        durable=False,
        exclusive=True,
        auto_delete=True,
    )
    jobs = []

    async def terminal_failure(_job):
        raise ColdPathOutputError("injected terminal model failure")

    with app.connection_for_write() as connection:
        bound_queue = observer(connection.channel())
        bound_queue.declare()
        try:
            for iteration in range(INJECTION_REPETITIONS):
                session_id = await _create_session(
                    runtime, owner_id, f"terminal-dlq-{iteration}"
                )
                job = await _enqueue(
                    runtime,
                    owner_id,
                    session_id,
                    f"terminal-dlq-{iteration}",
                )
                result = await _execute(runtime, job, terminal_failure)
                assert result == {
                    "job_id": str(job.id),
                    "status": JobStatus.FAILED.value,
                    "error_code": JobErrorCode.PROVIDER_ERROR.value,
                }
                DeadLetterPublisher(app).publish(
                    job_id=job.id,
                    error_code=result["error_code"],
                    original_queue="deepscout.cold",
                )
                jobs.append((job, session_id))

            messages = [
                bound_queue.get(no_ack=False)
                for _ in range(INJECTION_REPETITIONS)
            ]
            assert all(message is not None for message in messages)
            assert {message.payload["job_id"] for message in messages} == {
                str(job.id) for job, _ in jobs
            }
            assert all(
                set(message.payload)
                == {"job_id", "error_code", "original_queue"}
                for message in messages
            )
            for message in messages:
                message.ack()
        finally:
            bound_queue.delete(if_unused=False, if_empty=False)

    for job, session_id in jobs:
        async with runtime.session_scope() as session:
            persisted = await JobRepository(session).get_internal(job.id)
            report_count = await session.scalar(
                select(func.count())
                .select_from(InterviewReport)
                .where(InterviewReport.session_id == session_id)
            )
        assert persisted.status is JobStatus.FAILED
        assert persisted.error_code is JobErrorCode.PROVIDER_ERROR
        assert report_count == 0
