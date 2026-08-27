"""Dependency tracing with idempotent registration and safe attributes."""

import asyncio
import logging
import re

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import event

logger = logging.getLogger(__name__)

_REDIS_OPERATIONS = frozenset(
    {"DELETE", "EVAL", "EXPIRE", "GET", "INCR", "PING", "PTTL", "SET"}
)
_redis_failure_logged = False
_sqlalchemy_failure_logged = False
_SQL_OPERATIONS = frozenset(
    {"DELETE", "INSERT", "SELECT", "UPDATE", "WITH"}
)
_TABLE_PATTERN = re.compile(
    r"\b(?:FROM|INTO|JOIN|UPDATE)\s+([A-Za-z_][A-Za-z0-9_.]{0,127})",
    re.IGNORECASE,
)


def _sql_identity(statement: str) -> tuple[str, str | None]:
    tokens = statement.lstrip().split(maxsplit=1)
    operation = tokens[0].upper() if tokens else "OTHER"
    if operation not in _SQL_OPERATIONS:
        operation = "OTHER"
    matched = _TABLE_PATTERN.search(statement)
    table = matched.group(1).lower() if matched else None
    return operation, table


def _sanitize_sqlalchemy_span(
    _conn,
    _cursor,
    statement,
    _parameters,
    execution_context,
    _executemany,
) -> None:
    span = getattr(execution_context, "_otel_span", None)
    if span is None:
        span = trace.get_current_span()
    if not span.is_recording():
        return
    attributes = getattr(span, "_attributes", None)
    if attributes is not None:
        for key in ("db.statement", "db.query.text", "db.operation"):
            attributes.pop(key, None)
    operation, table = _sql_identity(statement)
    span.set_attribute("db.operation.name", operation)
    if table is not None:
        span.set_attribute("db.collection.name", table)
    span.update_name(f"{operation} {table}" if table else operation)


def instrument_sqlalchemy(
    engine,
    *,
    tracer_provider=None,
    meter_provider=None,
) -> bool:
    """Instrument one SQLAlchemy engine; repeated registration is a no-op."""
    global _sqlalchemy_failure_logged
    instrumentor = SQLAlchemyInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        return False
    sync_engine = getattr(engine, "sync_engine", None)
    if sync_engine is None:
        return False
    try:
        instrumentor.instrument(
            engine=sync_engine,
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            enable_commenter=False,
            enable_attribute_commenter=False,
        )
        event.listen(
            sync_engine,
            "before_cursor_execute",
            _sanitize_sqlalchemy_span,
        )
    except Exception as exc:
        if not _sqlalchemy_failure_logged:
            logger.error(
                "SQLAlchemy tracing disabled",
                extra={
                    "event": "dependency_instrumentation_failed",
                    "dependency": "postgresql",
                    "error_type": type(exc).__name__,
                },
            )
            _sqlalchemy_failure_logged = True
        return False
    return True


def _redis_operation(command) -> str:
    if isinstance(command, bytes):
        command = command.decode("ascii", errors="ignore")
    normalized = str(command).strip().upper()
    return normalized if normalized in _REDIS_OPERATIONS else "OTHER"


def _redis_error_type(exc: BaseException) -> str:
    if isinstance(exc, (RedisTimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, (RedisError, OSError)):
        return "connection"
    return "internal"


def instrument_redis_client(client, *, tracer_provider=None) -> bool:
    """Wrap Redis command execution without recording keys, values or URLs."""
    global _redis_failure_logged
    try:
        if getattr(client, "_deepscout_otel_instrumented", False):
            return False
        original = getattr(client, "execute_command", None)
        if original is None:
            return False
        tracer = (
            tracer_provider.get_tracer(__name__)
            if tracer_provider is not None
            else trace.get_tracer(__name__)
        )

        async def execute_command(command, *args, **kwargs):
            operation = _redis_operation(command)
            with tracer.start_as_current_span(
                f"redis {operation}",
                kind=SpanKind.CLIENT,
                attributes={
                    "db.system.name": "redis",
                    "db.operation.name": operation,
                },
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                try:
                    return await original(command, *args, **kwargs)
                except asyncio.CancelledError:
                    span.set_status(Status(StatusCode.ERROR))
                    span.set_attribute("error.type", "cancelled")
                    raise
                except Exception as exc:
                    span.set_status(Status(StatusCode.ERROR))
                    span.set_attribute("error.type", _redis_error_type(exc))
                    raise

        client.execute_command = execute_command
        client._deepscout_otel_instrumented = True
    except Exception as exc:
        if not _redis_failure_logged:
            logger.error(
                "Redis tracing disabled",
                extra={
                    "event": "dependency_instrumentation_failed",
                    "dependency": "redis",
                    "error_type": type(exc).__name__,
                },
            )
            _redis_failure_logged = True
        return False
    return True
