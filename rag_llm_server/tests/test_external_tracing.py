import asyncio
import uuid

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from observability.external_span import ExternalLLMCallbackHandler, external_call
from services import asr_client, llm_service, rtc_service


def _tracing():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _traced_external_call(tracer_provider):
    def factory(provider, operation, **kwargs):
        return external_call(
            provider,
            operation,
            tracer_provider=tracer_provider,
            **kwargs,
        )

    return factory


def test_external_span_records_only_bounded_success_metadata():
    provider, exporter = _tracing()

    with external_call(
        "ark",
        "chat",
        model="ep-interviewer",
        retry_count=0,
        input_size=23,
        tracer_provider=provider,
    ) as call:
        call.succeed(http_status=200, output_size=7)

    span = exporter.get_finished_spans()[0]
    assert span.name == "ark chat"
    assert span.attributes["external.provider"] == "ark"
    assert span.attributes["external.operation"] == "chat"
    assert span.attributes["external.model"] == "ep-interviewer"
    assert span.attributes["external.outcome"] == "success"
    assert span.attributes["http.response.status_code"] == 200
    assert span.attributes["external.input.size"] == 23
    assert span.attributes["external.output.size"] == 7
    assert span.attributes["external.retry_count"] == 0
    assert span.attributes["external.duration_ms"] >= 0


@pytest.mark.parametrize(
    ("provider", "exception", "expected"),
    [
        (
            "asr",
            httpx.ReadTimeout("audio=https://private.example/secret.wav"),
            "timeout",
        ),
        (
            "rtc",
            httpx.HTTPStatusError(
                "https://rtc.example/?Signature=secret",
                request=httpx.Request("POST", "https://rtc.example/"),
                response=httpx.Response(429),
            ),
            "rate_limited",
        ),
        ("ark", asyncio.CancelledError("prompt=must-not-leak"), "cancelled"),
    ],
)
def test_external_span_maps_failures_without_recording_sensitive_details(
    provider,
    exception,
    expected,
):
    tracer_provider, exporter = _tracing()

    with pytest.raises(type(exception)):
        with external_call(
            provider,
            "request",
            model="stable-model",
            tracer_provider=tracer_provider,
        ):
            raise exception

    span = exporter.get_finished_spans()[0]
    assert span.attributes["external.outcome"] == expected
    assert span.attributes["error.type"] == expected
    encoded = str((dict(span.attributes), span.events))
    assert "secret" not in encoded
    assert "must-not-leak" not in encoded
    assert "private.example" not in encoded


async def test_asr_client_success_span_excludes_audio_url(monkeypatch):
    tracer_provider, exporter = _tracing()
    monkeypatch.setattr(asr_client.settings, "ASR_FILE_API_KEY", "api-secret")
    monkeypatch.setattr(
        asr_client,
        "external_call",
        _traced_external_call(tracer_provider),
    )

    class Response:
        status_code = 200
        headers = {"X-Api-Status-Code": asr_client.STATUS_SUCCESS}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            del args, kwargs
            return Response()

    monkeypatch.setattr(asr_client.httpx, "AsyncClient", lambda **kwargs: Client())

    await asr_client.submit_asr(
        "https://private.example/audio/customer-secret.wav?token=secret",
        "wav",
    )

    span = exporter.get_finished_spans()[0]
    assert span.attributes["external.provider"] == "asr"
    assert span.attributes["external.outcome"] == "success"
    encoded = str((dict(span.attributes), span.events))
    assert "private.example" not in encoded
    assert "customer-secret" not in encoded


async def test_rtc_client_success_span_excludes_signed_request(monkeypatch):
    tracer_provider, exporter = _tracing()
    monkeypatch.setattr(
        rtc_service,
        "external_call",
        _traced_external_call(tracer_provider),
    )

    async def build_body(*args):
        del args
        return {"RoomId": "private-room", "Token": "rtc-secret"}

    class Signer:
        def __init__(self, *args):
            del args

        def add_authorization(self, credentials):
            del credentials

    class Lease:
        async def assert_owned(self):
            return None

    class Response:
        status_code = 200

        def json(self):
            return {"ResponseMetadata": {}}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            del args, kwargs
            return Response()

    monkeypatch.setattr(rtc_service, "build_voice_chat_body", build_body)
    monkeypatch.setattr(rtc_service, "Signer", Signer)
    monkeypatch.setattr(rtc_service.httpx, "AsyncClient", Client)

    await rtc_service._call_provider(
        "StartVoiceChat",
        "2024-12-01",
        {"id": "private-session"},
        {"message": "must-not-leak"},
        Lease(),
    )

    span = exporter.get_finished_spans()[0]
    assert span.attributes["external.provider"] == "rtc"
    assert span.attributes["external.outcome"] == "success"
    encoded = str((dict(span.attributes), span.events))
    assert "private-room" not in encoded
    assert "rtc-secret" not in encoded
    assert "must-not-leak" not in encoded


def test_ark_stream_timeout_is_traced_once_without_prompt(monkeypatch):
    tracer_provider, exporter = _tracing()
    monkeypatch.setattr(
        llm_service,
        "external_call",
        _traced_external_call(tracer_provider),
    )

    class Completions:
        calls = 0

        def create(self, **kwargs):
            del kwargs
            self.calls += 1
            raise httpx.ReadTimeout("prompt=customer-secret")

    completions = Completions()
    service = object.__new__(llm_service.LLMService)
    service.client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": completions})()},
    )()

    result = list(
        service.chat_stream(
            [{"role": "user", "content": "prompt=must-not-leak"}],
        )
    )

    assert result == [None]
    assert completions.calls == 1
    span = exporter.get_finished_spans()[0]
    assert span.attributes["external.provider"] == "ark"
    assert span.attributes["error.type"] == "timeout"
    encoded = str((dict(span.attributes), span.events))
    assert "customer-secret" not in encoded
    assert "must-not-leak" not in encoded


def test_active_ark_callback_records_token_counts_without_content():
    tracer_provider, exporter = _tracing()
    handler = ExternalLLMCallbackHandler(
        "ep-interviewer",
        tracer_provider=tracer_provider,
    )
    run_id = uuid.uuid4()

    handler.on_chat_model_start(
        {},
        [[{"role": "user", "content": "prompt=must-not-leak"}]],
        run_id=run_id,
    )
    response = type(
        "Response",
        (),
        {
            "llm_output": {
                "token_usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 5,
                }
            }
        },
    )()
    handler.on_llm_end(response, run_id=run_id)

    span = exporter.get_finished_spans()[0]
    assert span.attributes["external.input.size"] == 12
    assert span.attributes["external.output.size"] == 5
    assert span.attributes["external.outcome"] == "success"
    assert "must-not-leak" not in str((dict(span.attributes), span.events))
