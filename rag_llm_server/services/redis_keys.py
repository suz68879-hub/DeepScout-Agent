"""Redis key、TTL 与版本化 JSON 的唯一规范入口。"""
import hashlib
import json
import math
from collections.abc import Collection, Mapping
from typing import Any

SCHEMA_VERSION = 1
MAX_KEY_INPUT_BYTES = 4096
MAX_PAYLOAD_BYTES = 16 * 1024
SESSION_MAX_TTL_SECONDS = 7 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
REGISTER_WINDOW_SECONDS = 60 * 60
RTC_LEASE_SECONDS = 30
RTC_RENEW_SECONDS = 10
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
CALLBACK_REPLAY_TTL_SECONDS = 660
LLM_RATE_WINDOW_SECONDS = 60
CALLBACK_RATE_WINDOW_SECONDS = 60
_APP_ENVS = {"development", "test", "production"}


class RedisDataError(ValueError):
    """Redis key 或缓存 payload 不符合共享规范。"""


def _hashed_key(app_env: str, domain: str, purpose: str, *identifiers: str) -> str:
    if app_env not in _APP_ENVS:
        raise ValueError("APP_ENV must be development, test, or production")
    for identifier in identifiers:
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Redis key input must not be empty")
        if len(identifier.encode("utf-8")) > MAX_KEY_INPUT_BYTES:
            raise ValueError("Redis key input is too long")
    canonical = json.dumps(
        identifiers,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"deepscout:{app_env}:{domain}:{purpose}:{digest}"


def auth_session_key(app_env: str, token_digest: str) -> str:
    return _hashed_key(app_env, "auth", "session", token_digest)


def login_rate_limit_key(app_env: str, client_ip: str, username: str) -> str:
    return _hashed_key(app_env, "rate", "login", client_ip, username.lower())


def register_rate_limit_key(app_env: str, client_ip: str) -> str:
    return _hashed_key(app_env, "rate", "register", client_ip)


def rtc_lock_key(app_env: str, session_id: str) -> str:
    return _hashed_key(app_env, "rtc", "lock", session_id)


def rtc_fence_key(app_env: str, session_id: str) -> str:
    return _hashed_key(app_env, "rtc", "fence", session_id)


def callback_replay_key(app_env: str, event_id: str) -> str:
    return _hashed_key(app_env, "rtc", "callback-replay", event_id)


def llm_rate_limit_key(app_env: str, user_id: str) -> str:
    return _hashed_key(app_env, "rate", "llm", user_id)


def callback_rate_limit_key(app_env: str, client_ip: str, callback_id: str) -> str:
    return _hashed_key(app_env, "rate", "callback", client_ip, callback_id)


def idempotency_record_key(
    app_env: str,
    user_id: str,
    method: str,
    route: str,
    idempotency_key: str,
) -> str:
    return _hashed_key(
        app_env,
        "idempotency",
        "record",
        user_id,
        method.upper(),
        route,
        idempotency_key,
    )


def validate_ttl(ttl: int, *, maximum: int) -> int:
    if type(ttl) is not int or ttl <= 0 or ttl > maximum:
        raise ValueError(f"TTL must be an integer between 1 and {maximum}")
    return ttl


def session_cache_ttl(remaining_seconds: float) -> int:
    if (
        isinstance(remaining_seconds, bool)
        or not isinstance(remaining_seconds, (int, float))
        or not math.isfinite(remaining_seconds)
    ):
        raise ValueError("TTL must be a finite number")
    ttl = min(int(remaining_seconds), SESSION_MAX_TTL_SECONDS)
    return validate_ttl(ttl, maximum=SESSION_MAX_TTL_SECONDS)


def _allowed(allowed_fields: Collection[str]) -> set[str]:
    fields = set(allowed_fields)
    if "schema_version" in fields:
        raise RedisDataError("schema_version is reserved")
    return fields


def encode_payload(payload: Mapping[str, Any], *, allowed_fields: Collection[str]) -> str:
    allowed = _allowed(allowed_fields)
    if set(payload) - allowed:
        raise RedisDataError("Redis payload contains unknown fields")
    try:
        encoded = json.dumps(
            {"schema_version": SCHEMA_VERSION, **payload},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise RedisDataError("Redis payload is not valid JSON") from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise RedisDataError("Redis payload is too large")
    return encoded


def decode_payload(raw: str, *, allowed_fields: Collection[str]) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise RedisDataError("Redis payload is too large")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RedisDataError("Redis payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RedisDataError("Redis payload must be an object")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise RedisDataError("Redis payload schema is unsupported")
    allowed = _allowed(allowed_fields)
    if set(payload) - allowed - {"schema_version"}:
        raise RedisDataError("Redis payload contains unknown fields")
    return {key: value for key, value in payload.items() if key != "schema_version"}
