"""OpenTelemetry providers with allowlisted resources and bounded shutdown."""

import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry._logs import SeverityNumber, set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    ConsoleLogRecordExporter,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from logging_config import redact

_DEFAULT_SERVICE_NAME = "interview-api"
_DEFAULT_SERVICE_VERSION = "0.1.0"


@dataclass(frozen=True)
class TelemetryConfig:
    service_name: str
    service_version: str
    environment: str
    exporter: str
    endpoint: str | None
    insecure: bool

    @property
    def resource_attributes(self) -> dict[str, str]:
        return {
            "service.name": self.service_name,
            "service.version": self.service_version,
            "deployment.environment": self.environment,
        }

    @classmethod
    def from_environment(
        cls,
        environment: str,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "TelemetryConfig":
        values = os.environ if environ is None else environ
        locked_environment = environment.strip().lower()
        if not locked_environment:
            raise ValueError("deployment environment is required")

        service_name = values.get("OTEL_SERVICE_NAME", "").strip()
        if not service_name:
            if locked_environment == "production":
                raise ValueError("OTEL_SERVICE_NAME is required in production")
            service_name = _DEFAULT_SERVICE_NAME

        service_version = (
            values.get("OTEL_SERVICE_VERSION", "").strip()
            or _DEFAULT_SERVICE_VERSION
        )
        endpoint = values.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip() or None
        if locked_environment == "production" and endpoint is None:
            raise ValueError(
                "OTEL_EXPORTER_OTLP_ENDPOINT is required in production"
            )

        exporter = "otlp" if endpoint else "console"
        insecure = values.get("OTEL_EXPORTER_OTLP_INSECURE", "").strip().lower()
        return cls(
            service_name=service_name,
            service_version=service_version,
            environment=locked_environment,
            exporter=exporter,
            endpoint=endpoint,
            insecure=insecure in {"1", "true", "yes", "on"},
        )


class TelemetryRuntime:
    def __init__(
        self,
        providers: Sequence[object],
        *,
        sampler_name: str,
        logging_handler: logging.Handler | None = None,
    ) -> None:
        self._providers = tuple(providers)
        self._logging_handler = logging_handler
        self._shutdown = False
        self.sampler_name = sampler_name

    async def shutdown(self, *, timeout_seconds: float) -> bool:
        if self._shutdown:
            return True
        timeout_millis = max(1, int(timeout_seconds * 1000))

        def flush_and_shutdown() -> bool:
            succeeded = True
            for provider in self._providers:
                try:
                    provider.force_flush(timeout_millis=timeout_millis)
                except Exception:
                    succeeded = False
            if self._logging_handler is not None:
                logging.getLogger().removeHandler(self._logging_handler)
            for provider in self._providers:
                try:
                    provider.shutdown()
                except Exception:
                    succeeded = False
            return succeeded

        try:
            succeeded = await asyncio.wait_for(
                asyncio.to_thread(flush_and_shutdown),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return False
        self._shutdown = True
        return succeeded


class _OpenTelemetryLogHandler(logging.Handler):
    _SEVERITY = {
        logging.DEBUG: SeverityNumber.DEBUG,
        logging.INFO: SeverityNumber.INFO,
        logging.WARNING: SeverityNumber.WARN,
        logging.ERROR: SeverityNumber.ERROR,
        logging.CRITICAL: SeverityNumber.FATAL,
    }

    def __init__(self, provider: LoggerProvider) -> None:
        super().__init__(level=logging.NOTSET)
        self._provider = provider

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("opentelemetry", "grpc")):
            return
        attributes = {}
        for key in ("event", "error_code"):
            value = getattr(record, key, None)
            if isinstance(value, str) and len(value) <= 128:
                attributes[key] = value
        self._provider.get_logger(record.name).emit(
            timestamp=int(record.created * 1_000_000_000),
            severity_number=self._SEVERITY.get(
                record.levelno,
                SeverityNumber.UNSPECIFIED,
            ),
            severity_text=record.levelname,
            body=redact(record.getMessage()),
            attributes=attributes,
        )


_runtime: TelemetryRuntime | None = None


def _otlp_exporters(config: TelemetryConfig):
    options = {"endpoint": config.endpoint, "insecure": config.insecure}
    return (
        OTLPSpanExporter(**options),
        OTLPMetricExporter(**options),
        OTLPLogExporter(**options),
    )


def _console_exporters():
    return (
        ConsoleSpanExporter(),
        ConsoleMetricExporter(),
        ConsoleLogRecordExporter(),
    )


def initialize_telemetry(
    config: TelemetryConfig | None = None,
) -> TelemetryRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime

    locked_config = config or TelemetryConfig.from_environment("development")
    resource = Resource.create(locked_config.resource_attributes)
    if locked_config.exporter == "otlp":
        span_exporter, metric_exporter, log_exporter = _otlp_exporters(
            locked_config
        )
    else:
        span_exporter, metric_exporter, log_exporter = _console_exporters()

    meter_provider = MeterProvider(
        metric_readers=(PeriodicExportingMetricReader(metric_exporter),),
        resource=resource,
        shutdown_on_exit=False,
    )
    tracer_provider = TracerProvider(
        sampler=ALWAYS_ON,
        resource=resource,
        shutdown_on_exit=False,
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(span_exporter, meter_provider=meter_provider)
    )
    logger_provider = LoggerProvider(
        resource=resource,
        shutdown_on_exit=False,
    )
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(log_exporter, meter_provider=meter_provider)
    )

    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    set_logger_provider(logger_provider)
    handler = _OpenTelemetryLogHandler(logger_provider)
    logging.getLogger().addHandler(handler)

    _runtime = TelemetryRuntime(
        (tracer_provider, meter_provider, logger_provider),
        sampler_name="AlwaysOnSampler",
        logging_handler=handler,
    )
    return _runtime


async def shutdown_telemetry(*, timeout_seconds: float = 5) -> bool:
    if _runtime is None:
        return True
    return await _runtime.shutdown(timeout_seconds=timeout_seconds)
