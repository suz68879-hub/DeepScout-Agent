"""Privacy-safe spans for external AI, speech and RTC providers."""

import asyncio
import re
import time
from types import TracebackType

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

_PROVIDERS = frozenset({"ark", "asr", "rtc"})
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_name(value: str, *, maximum: int = 128) -> str:
    normalized = _SAFE_NAME.sub("_", str(value).strip())[:maximum]
    return normalized or "unknown"


def _error_type(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled", None
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout", None
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 429:
            return "rate_limited", status_code
        if status_code >= 500:
            return "upstream", status_code
        return "rejected", status_code
    if isinstance(exc, (httpx.TransportError, ConnectionError, OSError)):
        return "connection", None
    return "provider", None


class ExternalCallSpan:
    """Mutable outcome handle returned by :func:`external_call`."""

    def __init__(
        self,
        provider: str,
        operation: str,
        *,
        model: str,
        retry_count: int,
        input_size: int | None,
        tracer_provider,
    ) -> None:
        locked_provider = provider if provider in _PROVIDERS else "other"
        locked_operation = _safe_name(operation, maximum=64)
        tracer = (
            tracer_provider.get_tracer(__name__)
            if tracer_provider is not None
            else trace.get_tracer(__name__)
        )
        attributes: dict[str, str | int] = {
            "external.provider": locked_provider,
            "external.operation": locked_operation,
            "external.model": _safe_name(model),
            "external.retry_count": max(0, int(retry_count)),
        }
        if input_size is not None:
            attributes["external.input.size"] = max(0, int(input_size))
        self._span_manager = tracer.start_as_current_span(
            f"{locked_provider} {locked_operation}",
            kind=SpanKind.CLIENT,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        )
        self._span = None
        self._started_at = 0.0
        self._outcome_set = False

    def __enter__(self):
        self._started_at = time.perf_counter()
        self._span = self._span_manager.__enter__()
        return self

    def succeed(
        self,
        *,
        http_status: int | None = None,
        output_size: int | None = None,
    ) -> None:
        self._set_outcome("success", http_status, output_size)

    def fail(
        self,
        error_type: str = "provider",
        *,
        http_status: int | None = None,
    ) -> None:
        locked_error = (
            error_type
            if error_type
            in {
                "cancelled",
                "connection",
                "provider",
                "rate_limited",
                "rejected",
                "timeout",
                "upstream",
            }
            else "provider"
        )
        self._set_outcome(locked_error, http_status, None)
        self._span.set_status(Status(StatusCode.ERROR))
        self._span.set_attribute("error.type", locked_error)

    def _set_outcome(
        self,
        outcome: str,
        http_status: int | None,
        output_size: int | None,
    ) -> None:
        self._outcome_set = True
        self._span.set_attribute("external.outcome", outcome)
        if http_status is not None:
            self._span.set_attribute("http.response.status_code", int(http_status))
        if output_size is not None:
            self._span.set_attribute("external.output.size", max(0, int(output_size)))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc is not None:
            error_type, http_status = _error_type(exc)
            self.fail(error_type, http_status=http_status)
        elif not self._outcome_set:
            self.succeed()
        duration_ms = (time.perf_counter() - self._started_at) * 1000
        self._span.set_attribute("external.duration_ms", duration_ms)
        return self._span_manager.__exit__(exc_type, exc, traceback)


def external_call(
    provider: str,
    operation: str,
    *,
    model: str,
    retry_count: int = 0,
    input_size: int | None = None,
    tracer_provider=None,
) -> ExternalCallSpan:
    return ExternalCallSpan(
        provider,
        operation,
        model=model,
        retry_count=retry_count,
        input_size=input_size,
        tracer_provider=tracer_provider,
    )


class ExternalLLMCallbackHandler(BaseCallbackHandler):
    """Trace ChatOpenAI calls without retaining prompts or completions."""

    def __init__(self, model: str, *, tracer_provider=None) -> None:
        self._model = _safe_name(model)
        self._tracer = (
            tracer_provider.get_tracer(__name__)
            if tracer_provider is not None
            else trace.get_tracer(__name__)
        )
        self._calls: dict[object, tuple[object, float]] = {}

    def _start(self, run_id) -> None:
        if run_id in self._calls:
            return
        span = self._tracer.start_span(
            "ark chat",
            kind=SpanKind.CLIENT,
            attributes={
                "external.provider": "ark",
                "external.operation": "chat",
                "external.model": self._model,
                "external.retry_count": 0,
            },
        )
        self._calls[run_id] = (span, time.perf_counter())

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs) -> None:
        del serialized, prompts, kwargs
        self._start(run_id)

    def on_chat_model_start(
        self,
        serialized,
        messages,
        *,
        run_id,
        **kwargs,
    ) -> None:
        del serialized, messages, kwargs
        self._start(run_id)

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        del kwargs
        call = self._calls.pop(run_id, None)
        if call is None:
            return
        span, started_at = call
        usage = (getattr(response, "llm_output", None) or {}).get(
            "token_usage",
            {},
        )
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if isinstance(prompt_tokens, int):
            span.set_attribute("external.input.size", max(0, prompt_tokens))
        if isinstance(completion_tokens, int):
            span.set_attribute("external.output.size", max(0, completion_tokens))
        span.set_attribute("external.outcome", "success")
        span.set_attribute(
            "external.duration_ms",
            (time.perf_counter() - started_at) * 1000,
        )
        span.end()

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        del kwargs
        call = self._calls.pop(run_id, None)
        if call is None:
            return
        span, started_at = call
        error_type, http_status = _error_type(error)
        span.set_attribute("external.outcome", error_type)
        span.set_attribute("error.type", error_type)
        if http_status is not None:
            span.set_attribute("http.response.status_code", http_status)
        span.set_attribute(
            "external.duration_ms",
            (time.perf_counter() - started_at) * 1000,
        )
        span.set_status(Status(StatusCode.ERROR))
        span.end()
