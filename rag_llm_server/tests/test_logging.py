"""结构化日志字段、请求关联和敏感信息脱敏。"""
import json
import logging

from logging_config import JsonFormatter
from middleware.request_context import request_id_context


def _format(message: str, **extra) -> dict:
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(JsonFormatter(service="interview-api", environment="test").format(record))


def test_json_log_has_required_correlation_fields():
    with request_id_context("request-42"):
        payload = _format("database ready", event="database_ready", error_code="DB_TEST")
    assert payload["service"] == "interview-api"
    assert payload["environment"] == "test"
    assert payload["event"] == "database_ready"
    assert payload["request_id"] == "request-42"
    assert payload["trace_id"] is None
    assert payload["error_code"] == "DB_TEST"
    assert payload["timestamp"].endswith("Z")


def test_json_log_redacts_secret_and_pii_value_patterns():
    payload = _format(
        "password=hunter2 Authorization: Bearer secret-token "
        "alice@example.com 13800138000 https://bucket.example/recording.wav"
    )
    encoded = json.dumps(payload, ensure_ascii=False)
    for leaked in ("hunter2", "secret-token", "alice@example.com", "13800138000", "recording.wav"):
        assert leaked not in encoded
    assert "[REDACTED]" in encoded


def test_json_log_redacts_sensitive_extra_fields():
    payload = _format("login rejected", event="login_rejected", password="secret", token="abc")
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "secret" not in encoded
    assert '"abc"' not in encoded

