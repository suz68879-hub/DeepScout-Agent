import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from opentelemetry.propagate import inject
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from logging_config import JsonFormatter
from middleware.request_context import (
    RequestContextMiddleware,
    get_job_id,
    get_span_id,
    get_trace_id,
    job_consumer_span,
    job_id_context,
    request_id_context,
)
from services.jobs.dispatcher import _trace_context
from services.jobs.handlers import JobType
from services.jobs.outbox import CeleryOutboxPublisher, OutboxRecord


def _tracing():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def _app(tracer) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware, tracer=tracer)

    @app.get("/context")
    async def context():
        return {"trace_id": get_trace_id(), "span_id": get_span_id()}

    return app


async def test_http_span_continues_valid_w3c_parent():
    tracer, exporter = _tracing()
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    parent_span_id = "b7ad6b7169203331"
    transport = httpx.ASGITransport(app=_app(tracer))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/context",
            headers={
                "traceparent": f"00-{trace_id}-{parent_span_id}-01",
                "baggage": "password=must-not-propagate",
            },
        )

    assert response.json()["trace_id"] == trace_id
    span = exporter.get_finished_spans()[0]
    assert f"{span.context.trace_id:032x}" == trace_id
    assert f"{span.parent.span_id:016x}" == parent_span_id
    assert "password" not in str(span.attributes)


async def test_invalid_traceparent_starts_new_trace_and_logs_security_event(caplog):
    tracer, exporter = _tracing()
    transport = httpx.ASGITransport(app=_app(tracer))
    caplog.set_level(logging.WARNING)

    with tracer.start_as_current_span("unrelated-ambient"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/context",
                headers={"traceparent": "00-invalid-parent-01"},
            )

    assert len(response.json()["trace_id"]) == 32
    assert exporter.get_finished_spans()[0].parent is None
    assert "invalid-parent" not in caplog.text
    assert "trace_context_rejected" in [
        getattr(record, "event", None) for record in caplog.records
    ]


def test_json_logs_read_active_trace_span_request_and_job_context():
    tracer, _ = _tracing()
    formatter = JsonFormatter(service="interview-api", environment="test")
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "job running", (), None
    )

    with tracer.start_as_current_span("consumer") as span:
        with request_id_context("request-42"), job_id_context("job-42"):
            payload = json.loads(formatter.format(record))

        assert payload["trace_id"] == f"{span.get_span_context().trace_id:032x}"
        assert payload["span_id"] == f"{span.get_span_context().span_id:016x}"
    assert payload["request_id"] == "request-42"
    assert payload["job_id"] == "job-42"


def test_consumer_span_extracts_parent_and_scopes_job_id():
    tracer, exporter = _tracing()
    carrier = {}
    with tracer.start_as_current_span("producer") as producer:
        producer_span_id = producer.get_span_context().span_id
        trace_id = producer.get_span_context().trace_id
        inject(carrier)

    with job_consumer_span(
        headers=carrier,
        job_id="job-42",
        operation="interview.finish",
        tracer=tracer,
    ):
        assert get_trace_id() == f"{trace_id:032x}"
        assert get_job_id() == "job-42"

    consumer = exporter.get_finished_spans()[-1]
    assert consumer.parent.span_id == producer_span_id
    assert consumer.attributes["job.id"] == "job-42"
    assert get_job_id() is None


def test_dispatcher_persists_only_w3c_trace_context_without_baggage():
    tracer, _ = _tracing()

    with tracer.start_as_current_span("request"):
        carrier = _trace_context()

    assert set(carrier) <= {"traceparent", "tracestate"}
    assert carrier["traceparent"].startswith("00-")
    assert "baggage" not in carrier


async def test_outbox_publisher_moves_trace_context_to_headers_only():
    calls = []
    tracer, exporter = _tracing()

    class FakeApp:
        def send_task(self, name, **options):
            calls.append((name, options))

    job_id = uuid.uuid4()
    trace_context = {
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "tracestate": "vendor=value",
    }
    event = OutboxRecord(
        id=uuid.uuid4(),
        aggregate_type="background_job",
        aggregate_id=job_id,
        event_type="job.created",
        payload={
            "schema_version": 1,
            "job_id": str(job_id),
            "job_type": JobType.INTERVIEW_FINISH.value,
            "trace_context": trace_context,
        },
        published_at=None,
        attempt=0,
        next_attempt_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )

    with tracer.start_as_current_span("dispatcher"):
        await CeleryOutboxPublisher(FakeApp(), tracer=tracer).publish(event)

    options = calls[0][1]
    assert options["kwargs"] == {
        "schema_version": 1,
        "job_id": str(job_id),
        "job_type": JobType.INTERVIEW_FINISH.value,
    }
    assert options["headers"]["traceparent"].split("-")[1] == (
        trace_context["traceparent"].split("-")[1]
    )
    assert options["headers"]["traceparent"] != trace_context["traceparent"]
    assert "baggage" not in options["headers"]
    producer = exporter.get_finished_spans()[0]
    assert producer.kind.name == "PRODUCER"
    assert f"{producer.parent.span_id:016x}" == "b7ad6b7169203331"


async def test_outbox_publisher_rejects_invalid_parent_without_dropping_job(caplog):
    calls = []
    tracer, exporter = _tracing()

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
            "trace_context": {"traceparent": "00-invalid-parent-01"},
        },
        published_at=None,
        attempt=0,
        next_attempt_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    caplog.set_level(logging.WARNING)

    await CeleryOutboxPublisher(FakeApp(), tracer=tracer).publish(event)

    assert len(calls) == 1
    assert calls[0][1]["headers"]["traceparent"].startswith("00-")
    assert exporter.get_finished_spans()[0].parent is None
    assert "invalid-parent" not in caplog.text
    assert "trace_context_rejected" in [
        getattr(record, "event", None) for record in caplog.records
    ]
