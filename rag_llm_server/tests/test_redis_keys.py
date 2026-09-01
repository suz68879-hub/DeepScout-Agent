"""Redis key、TTL 与版本化 JSON 规范测试。"""
import json

import pytest

from services.redis_keys import (
    IDEMPOTENCY_TTL_SECONDS,
    LOGIN_WINDOW_SECONDS,
    REGISTER_WINDOW_SECONDS,
    RTC_LEASE_SECONDS,
    RTC_RENEW_SECONDS,
    CALLBACK_REPLAY_TTL_SECONDS,
    RedisDataError,
    auth_session_key,
    decode_payload,
    encode_payload,
    idempotency_record_key,
    login_rate_limit_key,
    register_rate_limit_key,
    rtc_fence_key,
    rtc_lock_key,
    callback_replay_key,
    session_cache_ttl,
    validate_ttl,
)
from services import redis_keys as redis_keys_mod


def test_login_key_has_stable_hash_and_normalizes_username():
    key = login_rate_limit_key("test", "203.0.113.9", "Alice")

    assert key == (
        "deepscout:test:rate:login:"
        "e7b2b6371c31fc68471fefdc832ec2aa42cbc49b9ad187b731e76740342e86e3"
    )
    assert key == login_rate_limit_key("test", "203.0.113.9", "alice")


def test_keys_are_environment_isolated_and_contain_no_plain_identifiers():
    raw_values = ("token-value", "203.0.113.9", "Alice", "session-123", "idem-456")
    keys = [
        auth_session_key("test", raw_values[0]),
        login_rate_limit_key("test", raw_values[1], raw_values[2]),
        register_rate_limit_key("test", raw_values[1]),
        rtc_lock_key("test", raw_values[3]),
        rtc_fence_key("test", raw_values[3]),
        callback_replay_key("test", "event-id-1"),
        redis_keys_mod.llm_rate_limit_key("test", "user-1"),
        redis_keys_mod.callback_rate_limit_key("test", raw_values[1], "callback-1"),
        idempotency_record_key("test", "user-1", "POST", "/api/interview", raw_values[4]),
    ]

    assert auth_session_key("production", raw_values[0]) != keys[0]
    assert len(set(keys)) == len(keys)
    for key in keys:
        assert key.startswith("deepscout:test:")
        assert all(value not in key for value in (*raw_values, "event-id-1", "callback-1", "user-1"))


@pytest.mark.parametrize("app_env", ["", "staging", "test:other"])
def test_key_rejects_unknown_or_unsafe_environment(app_env):
    with pytest.raises(ValueError, match="APP_ENV"):
        auth_session_key(app_env, "token")


def test_key_rejects_empty_and_oversized_input():
    with pytest.raises(ValueError, match="must not be empty"):
        auth_session_key("test", "")
    with pytest.raises(ValueError, match="too long"):
        auth_session_key("test", "x" * 4097)


def test_locked_ttl_values_and_session_cap():
    assert LOGIN_WINDOW_SECONDS == 15 * 60
    assert REGISTER_WINDOW_SECONDS == 60 * 60
    assert RTC_LEASE_SECONDS == 30
    assert RTC_RENEW_SECONDS == 10
    assert IDEMPOTENCY_TTL_SECONDS == 24 * 60 * 60
    assert CALLBACK_REPLAY_TTL_SECONDS == 660
    assert redis_keys_mod.LLM_RATE_WINDOW_SECONDS == 60
    assert redis_keys_mod.CALLBACK_RATE_WINDOW_SECONDS == 60
    assert session_cache_ttl(15.9) == 15
    assert session_cache_ttl(8 * 24 * 60 * 60) == 7 * 24 * 60 * 60


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5, "30"])
def test_ttl_rejects_non_positive_or_non_integer_values(ttl):
    with pytest.raises(ValueError, match="TTL"):
        validate_ttl(ttl, maximum=30)


def test_ttl_rejects_value_over_cap():
    with pytest.raises(ValueError, match="TTL"):
        validate_ttl(31, maximum=30)
    with pytest.raises(ValueError, match="TTL"):
        session_cache_ttl(0)


def test_json_payload_is_versioned_deterministic_and_allowlisted():
    encoded = encode_payload(
        {"user_id": "user-1", "username": "alice"},
        allowed_fields={"user_id", "username"},
    )

    assert encoded == '{"schema_version":1,"user_id":"user-1","username":"alice"}'
    assert decode_payload(encoded, allowed_fields={"user_id", "username"}) == {
        "user_id": "user-1",
        "username": "alice",
    }


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        '{"schema_version":2,"user_id":"user-1"}',
        '{"schema_version":1,"password_hash":"secret"}',
    ],
)
def test_json_payload_rejects_corruption_unknown_schema_and_fields(raw):
    with pytest.raises(RedisDataError):
        decode_payload(raw, allowed_fields={"user_id", "username"})


def test_json_payload_rejects_unknown_fields_and_oversized_data():
    with pytest.raises(RedisDataError, match="fields"):
        encode_payload({"token": "secret"}, allowed_fields={"user_id"})
    oversized = json.dumps({"schema_version": 1, "user_id": "x" * 17000})
    with pytest.raises(RedisDataError, match="too large"):
        decode_payload(oversized, allowed_fields={"user_id"})
