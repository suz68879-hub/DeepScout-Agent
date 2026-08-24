"""Single-line JSON logging with request correlation and PII redaction."""
import json
import logging
import re
from datetime import datetime, timezone

from middleware.request_context import get_request_id

_STANDARD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {
    "message", "asctime", "event", "trace_id", "error_code",
}
_SENSITIVE_KEYS = {
    "password", "passwd", "cookie", "authorization", "token", "secret",
    "phone", "mobile", "email", "resume", "recording_url",
}
_PATTERNS = (
    re.compile(r"(?i)(password|passwd|token|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"https?://[^\s,;]+", re.IGNORECASE),
)


def redact(value: str) -> str:
    result = value
    for pattern in _PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _safe_value(key: str, value):
    if any(fragment in key.lower() for fragment in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_safe_value(key, item) for item in value]
    if isinstance(value, dict):
        return {str(item_key): _safe_value(str(item_key), item_value) for item_key, item_value in value.items()}
    return {"unsupported_type": type(value).__name__}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str):
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": self.service,
            "environment": self.environment,
            "event": getattr(record, "event", "log"),
            "request_id": get_request_id(),
            "trace_id": getattr(record, "trace_id", None),
            "error_code": getattr(record, "error_code", None),
            "message": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS:
                payload[key] = _safe_value(key, value)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(environment: str, log_format: str = "json") -> None:
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter(service="interview-api", environment=environment))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
