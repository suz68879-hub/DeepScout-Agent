import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from middleware.request_context import RequestContextMiddleware, job_consumer_span
from observability.external_span import external_call
from services.jobs.dispatcher import _trace_context
from services.jobs.handlers import JobType
from services.jobs.outbox import CeleryOutboxPublisher, OutboxRecord


async def test_api_to_rabbit_worker_and_external_dependency_share_one_trace():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("phase-4-acceptance")
    published = {}

    class Broker:
        def send_task(self, name, **options):
            published.update({"name": name, **options})

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware, tracer=tracer)

    @app.post("/enqueue")
    async def enqueue():
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
                "trace_context": _trace_context(),
            },
            published_at=None,
            attempt=0,
            next_attempt_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        await CeleryOutboxPublisher(Broker(), tracer=tracer).publish(event)
        return {"job_id": str(job_id)}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post("/enqueue")
    job_id = response.json()["job_id"]

    with job_consumer_span(
        headers=published["headers"],
        job_id=job_id,
        operation=JobType.INTERVIEW_FINISH.value,
        tracer=tracer,
    ):
        with external_call(
            "ark",
            "chat",
            model="stable-model",
            tracer_provider=provider,
        ) as call:
            call.succeed(http_status=200, output_size=4)

    spans = exporter.get_finished_spans()
    by_name = {span.name: span for span in spans}
    server = by_name["POST /enqueue"]
    producer = by_name["deepscout.cold publish"]
    consumer = by_name["interview.finish process"]
    external = by_name["ark chat"]

    assert {span.context.trace_id for span in spans} == {server.context.trace_id}
    assert producer.parent.span_id == server.context.span_id
    assert consumer.parent.span_id == producer.context.span_id
    assert external.parent.span_id == consumer.context.span_id
    assert consumer.attributes["job.id"] == job_id
    assert external.attributes["external.outcome"] == "success"
