import asyncio
import logging
from pathlib import Path

import pytest
import yaml

import main
from observability.telemetry import (
    TelemetryConfig,
    TelemetryRuntime,
    _OpenTelemetryLogHandler,
    initialize_telemetry,
    shutdown_telemetry,
)


def test_non_production_config_uses_safe_resource_allowlist():
    config = TelemetryConfig.from_environment(
        "test",
        environ={
            "OTEL_RESOURCE_ATTRIBUTES": "password=secret,user.id=123",
        },
    )

    assert config.exporter == "console"
    assert config.resource_attributes == {
        "service.name": "interview-api",
        "service.version": "0.1.0",
        "deployment.environment": "test",
    }
    assert "password" not in str(config.resource_attributes)
    assert "user.id" not in str(config.resource_attributes)


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        (
            {
                "OTEL_SERVICE_VERSION": "1.2.3",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "collector:4317",
            },
            "OTEL_SERVICE_NAME",
        ),
        (
            {
                "OTEL_SERVICE_NAME": "interview-api",
                "OTEL_SERVICE_VERSION": "1.2.3",
            },
            "OTEL_EXPORTER_OTLP_ENDPOINT",
        ),
    ],
)
def test_production_config_requires_identity_and_otlp_endpoint(environ, message):
    with pytest.raises(ValueError, match=message):
        TelemetryConfig.from_environment("production", environ=environ)


def test_production_config_uses_otlp_and_sdk_always_on_sampling():
    config = TelemetryConfig.from_environment(
        "production",
        environ={
            "OTEL_SERVICE_NAME": "interview-api",
            "OTEL_SERVICE_VERSION": "1.2.3",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "collector:4317",
        },
    )

    runtime = initialize_telemetry(config)
    try:
        assert config.exporter == "otlp"
        assert runtime.sampler_name == "AlwaysOnSampler"
    finally:
        asyncio.run(shutdown_telemetry(timeout_seconds=1))


def test_initialization_is_idempotent():
    config = TelemetryConfig.from_environment("test", environ={})

    first = initialize_telemetry(config)
    second = initialize_telemetry(config)
    try:
        assert second is first
    finally:
        asyncio.run(shutdown_telemetry(timeout_seconds=1))


async def test_runtime_shutdown_flushes_each_provider_with_a_bound():
    events = []

    class Provider:
        def force_flush(self, timeout_millis):
            events.append(("flush", timeout_millis))

        def shutdown(self):
            events.append(("shutdown", None))

    runtime = TelemetryRuntime([Provider(), Provider()], sampler_name="test")

    assert await runtime.shutdown(timeout_seconds=0.5) is True
    assert events == [
        ("flush", 500),
        ("flush", 500),
        ("shutdown", None),
        ("shutdown", None),
    ]


async def test_runtime_shutdown_contains_exporter_failures():
    class FailingProvider:
        def force_flush(self, timeout_millis):
            raise OSError("collector unavailable")

        def shutdown(self):
            raise OSError("collector unavailable")

    runtime = TelemetryRuntime([FailingProvider()], sampler_name="test")

    assert await runtime.shutdown(timeout_seconds=0.5) is False


def test_log_bridge_redacts_messages_and_ignores_otel_internal_logs():
    exported = []

    class Provider:
        def get_logger(self, name):
            return self

        def emit(self, **payload):
            exported.append(payload)

    handler = _OpenTelemetryLogHandler(Provider())
    internal = logging.LogRecord(
        "opentelemetry.exporter",
        logging.ERROR,
        "",
        0,
        "collector unavailable",
        (),
        None,
    )
    application = logging.LogRecord(
        "application",
        logging.ERROR,
        "",
        0,
        "token=super-secret",
        (),
        None,
    )
    application.event = "provider_failed"
    application.error_code = "UPSTREAM_ERROR"

    handler.emit(internal)
    handler.emit(application)

    assert len(exported) == 1
    assert exported[0]["body"] == "[REDACTED]"
    assert exported[0]["attributes"] == {
        "event": "provider_failed",
        "error_code": "UPSTREAM_ERROR",
    }


async def test_application_lifespan_wraps_resources_with_telemetry(monkeypatch):
    events = []

    async def record(name):
        events.append(name)

    def initialize(config):
        events.append(("telemetry_init", config.environment))

    async def shutdown(*, timeout_seconds):
        events.append(("telemetry_shutdown", timeout_seconds))
        return True

    monkeypatch.setattr(main, "initialize_telemetry", initialize)
    monkeypatch.setattr(main, "shutdown_telemetry", shutdown)
    monkeypatch.setattr(main, "init_database", lambda: record("database_init"))
    monkeypatch.setattr(main, "init_redis", lambda: record("redis_init"))
    monkeypatch.setattr(main, "init_storage", lambda: record("storage_init"))
    monkeypatch.setattr(main, "init_graph", lambda: record("graph_init"))
    monkeypatch.setattr(main, "close_graph", lambda: record("graph_close"))
    monkeypatch.setattr(main, "close_redis", lambda: record("redis_close"))
    monkeypatch.setattr(main, "close_storage", lambda: record("storage_close"))
    monkeypatch.setattr(main, "close_database", lambda: record("database_close"))
    monkeypatch.setattr(main.registry, "render_all", lambda: None)
    monkeypatch.setattr(main.settings, "RTC_CALLBACK_SECRET", "test-secret")

    async with main.lifespan(main.create_app()):
        pass

    assert events == [
        ("telemetry_init", main.settings.APP_ENV),
        "database_init",
        "redis_init",
        "storage_init",
        "graph_init",
        "graph_close",
        "redis_close",
        "storage_close",
        "database_close",
        ("telemetry_shutdown", 5),
    ]


def test_collector_has_isolated_pipelines_and_tail_sampling():
    path = Path(__file__).parents[2] / "observability" / "otel-collector.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))

    pipelines = config["service"]["pipelines"]
    assert set(pipelines) == {"traces", "metrics", "logs"}
    assert pipelines["traces"]["receivers"] == ["otlp"]
    assert "tail_sampling" in pipelines["traces"]["processors"]
    assert "tail_sampling" not in pipelines["metrics"]["processors"]

    policies = config["processors"]["tail_sampling"]["policies"]
    assert {policy["name"] for policy in policies} == {
        "errors",
        "high-latency",
        "normal-traffic",
    }
    normal = next(policy for policy in policies if policy["name"] == "normal-traffic")
    assert normal["probabilistic"]["sampling_percentage"] == 10
