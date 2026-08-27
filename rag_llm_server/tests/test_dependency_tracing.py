import uuid
from datetime import datetime, timezone

import pytest
import observability.instrumentation as dependency_instrumentation
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from observability.instrumentation import (
    instrument_redis_client,
    instrument_sqlalchemy,
)
from services.jobs.handlers import JobType
from services.jobs.outbox import CeleryOutboxPublisher, OutboxRecord


def _tracing():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


@pytest.fixture(autouse=True)
def reset_sqlalchemy_instrumentation():
    instrumentor = SQLAlchemyInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    yield
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()


async def test_sqlalchemy_spans_hide_dsn_and_bind_values():
    provider, exporter = _tracing()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    assert instrument_sqlalchemy(engine, tracer_provider=provider) is True
    assert instrument_sqlalchemy(engine, tracer_provider=provider) is False

    async with engine.connect() as connection:
        await connection.execute(text("SELECT :value"), {"value": "super-secret"})
    await engine.dispose()

    spans = exporter.get_finished_spans()
    assert spans
    encoded = str([(span.name, dict(span.attributes)) for span in spans])
    assert "super-secret" not in encoded
    assert "sqlite+aiosqlite://" not in encoded
    assert "db.statement" not in encoded
    assert "db.query.text" not in encoded
    assert any(
        span.attributes.get("db.operation.name") == "SELECT" for span in spans
    ), [(span.name, dict(span.attributes)) for span in spans]


async def test_sqlalchemy_error_span_has_stable_error_without_bind_values():
    provider, exporter = _tracing()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    instrument_sqlalchemy(engine, tracer_provider=provider)

    with pytest.raises(OperationalError):
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT * FROM missing_table WHERE token=:token"),
                {"token": "must-not-leak"},
            )
    await engine.dispose()

    error = next(span for span in exporter.get_finished_spans() if not span.status.is_ok)
    encoded = str((error.name, dict(error.attributes), error.events))
    assert "must-not-leak" not in encoded


class FakeRedis:
    def __init__(self, outcomes):
        self._outcomes = iter(outcomes)

    async def execute_command(self, *args, **kwargs):
        del args, kwargs
        outcome = next(self._outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def test_redis_spans_record_operation_and_safe_outcomes_without_key():
    provider, exporter = _tracing()
    client = FakeRedis(
        [
            "value",
            RedisTimeoutError("secret-key timed out"),
            RedisConnectionError("redis://user:password@cache failed"),
        ]
    )
    assert instrument_redis_client(client, tracer_provider=provider) is True
    assert instrument_redis_client(client, tracer_provider=provider) is False

    assert await client.execute_command("GET", "tenant:secret-key") == "value"
    with pytest.raises(RedisTimeoutError):
        await client.execute_command("GET", "tenant:secret-key")
    with pytest.raises(RedisConnectionError):
        await client.execute_command("GET", "tenant:secret-key")

    spans = exporter.get_finished_spans()
    assert [span.attributes["db.operation.name"] for span in spans] == [
        "GET",
        "GET",
        "GET",
    ]
    assert [span.attributes.get("error.type") for span in spans] == [
        None,
        "timeout",
        "connection",
    ]
    encoded = str([(span.name, dict(span.attributes), span.events) for span in spans])
    assert "tenant:secret-key" not in encoded
    assert "password" not in encoded


def test_redis_instrumentation_failure_logs_once_without_client_details(
    caplog,
    monkeypatch,
):
    class ReadOnlyRedis:
        __slots__ = ()

        async def execute_command(self, *args, **kwargs):
            del args, kwargs

    monkeypatch.setattr(
        dependency_instrumentation,
        "_redis_failure_logged",
        False,
    )
    caplog.set_level("ERROR")

    assert instrument_redis_client(ReadOnlyRedis()) is False
    assert instrument_redis_client(ReadOnlyRedis()) is False

    records = [
        record
        for record in caplog.records
        if getattr(record, "dependency", None) == "redis"
    ]
    assert len(records) == 1
    assert records[0].event == "dependency_instrumentation_failed"
    assert records[0].error_type == "AttributeError"
    assert "redis://" not in records[0].getMessage()


async def test_broker_error_span_does_not_record_dsn_or_message_body():
    provider, exporter = _tracing()
    tracer = provider.get_tracer("test")

    class FailingApp:
        def send_task(self, name, **options):
            del name, options
            raise OSError(
                "amqp://user:password@broker payload=must-not-leak"
            )

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

    with pytest.raises(OSError):
        await CeleryOutboxPublisher(FailingApp(), tracer=tracer).publish(event)

    span = exporter.get_finished_spans()[0]
    assert span.attributes["error.type"] == "connection"
    encoded = str((dict(span.attributes), span.events))
    assert "password" not in encoded
    assert "must-not-leak" not in encoded
