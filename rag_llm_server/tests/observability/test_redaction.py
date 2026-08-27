import json
import logging

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from prometheus_client import CollectorRegistry, generate_latest

from logging_config import JsonFormatter
from observability.external_span import external_call
from observability.metrics import ServiceMetrics


def test_log_span_and_metric_exporters_have_zero_sensitive_sample_hits():
    samples = (
        "customer-password-123",
        "private.example/audio/customer.wav",
        "candidate@example.com",
        "13800138000",
        "prompt-must-not-leak",
    )
    formatter = JsonFormatter(service="interview-api", environment="test")
    record = logging.LogRecord(
        "acceptance",
        logging.ERROR,
        __file__,
        1,
        (
            "password=customer-password-123 "
            "url=https://private.example/audio/customer.wav "
            "email=candidate@example.com phone=13800138000"
        ),
        (),
        None,
    )
    log_output = formatter.format(record)
    assert json.loads(log_output)["message"]

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with pytest.raises(httpx.ReadTimeout):
        with external_call(
            "ark",
            "chat",
            model="stable-model",
            tracer_provider=provider,
        ):
            raise httpx.ReadTimeout("prompt-must-not-leak")
    span_output = str(
        [
            (span.name, dict(span.attributes), span.events)
            for span in exporter.get_finished_spans()
        ]
    )

    metrics = ServiceMetrics(CollectorRegistry())
    metrics.record_external(
        "private.example/audio/customer.wav",
        "prompt-must-not-leak",
        "unexpected",
        0.1,
    )
    metric_output = generate_latest(metrics.registry).decode("utf-8")

    exported = "\n".join((log_output, span_output, metric_output))
    for sample in samples:
        assert sample not in exported
