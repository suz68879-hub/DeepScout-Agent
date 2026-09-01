"""回调验签：排序拼接、字段缺省、messages 绑定与时间窗。"""
import hashlib
import types
from datetime import datetime, timezone

import services.callback_verify as callback_verify
from services.callback_verify import SIGN_FIELDS, compute_signature, verify_callback

NOW = 1_700_000_000


def _freeze_now(monkeypatch):
    monkeypatch.setattr(
        callback_verify,
        "time",
        types.SimpleNamespace(time=lambda: NOW),
        raising=False,
    )


def _signed_body(secret="sk", **over):
    body = {
        "AppId": "app1",
        "EventData": "d",
        "EventId": "e",
        "EventTime": str(NOW),
        "EventType": "v",
        "Nonce": "1234",
        "Version": "2020-12-01",
        "messages": [{"role": "user", "content": "你好"}],
    }
    body.update(over)
    body["Signature"] = compute_signature(body, secret)
    return body


def test_signature_without_messages_matches_official_seven_fields():
    payload = "app1" + "d" + "e" + "t" + "v" + "1234" + "sk" + "2020-12-01"
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    body = {
        "AppId": "app1", "EventData": "d", "EventId": "e", "EventTime": "t",
        "EventType": "v", "Nonce": "1234", "Version": "2020-12-01",
    }
    assert "messages" not in body
    assert compute_signature(body, "sk") == expected


def test_compute_signature_binds_canonical_messages():
    # 字母序含 Messages 与 SecretKey：[AppId, EventData, EventId, EventTime, EventType, Messages, Nonce, SecretKey, Version]
    messages = '[{"content":"你好","role":"user"}]'
    payload = (
        "app1" + "d" + "e" + str(NOW) + "v" + messages + "1234" + "sk" + "2020-12-01"
    )
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    body = {
        "AppId": "app1",
        "EventData": "d",
        "EventId": "e",
        "EventTime": str(NOW),
        "EventType": "v",
        "Nonce": "1234",
        "Version": "2020-12-01",
        "messages": [{"role": "user", "content": "你好"}],
    }
    assert compute_signature(body, "sk") == expected


def test_verify_accepts_valid_signature(monkeypatch):
    _freeze_now(monkeypatch)
    assert verify_callback(_signed_body(), "sk") is True


def test_verify_rejects_tampered_messages():
    body = _signed_body()
    body["messages"] = [{"role": "user", "content": "伪造的回答"}]
    assert verify_callback(body, "sk") is False


def test_verify_rejects_stale_event_time(monkeypatch):
    _freeze_now(monkeypatch)
    body = _signed_body(EventTime=str(NOW - 301))
    assert verify_callback(body, "sk") is False


def test_verify_rejects_event_time_too_far_in_future(monkeypatch):
    _freeze_now(monkeypatch)
    body = _signed_body(EventTime=str(NOW + 301))
    assert verify_callback(body, "sk") is False


def test_verify_rejects_missing_replay_id(monkeypatch):
    _freeze_now(monkeypatch)
    body = _signed_body(EventId="", Nonce="")
    assert verify_callback(body, "sk") is False


def test_verify_accepts_iso_event_time(monkeypatch):
    _freeze_now(monkeypatch)
    iso = datetime.fromtimestamp(NOW, timezone.utc).isoformat()
    assert verify_callback(_signed_body(EventTime=iso), "sk") is True


def test_verify_rejects_tampered_body():
    body = _signed_body()
    body["EventData"] = "hacked"
    assert verify_callback(body, "sk") is False


def test_verify_rejects_wrong_secret():
    assert verify_callback(_signed_body(), "wrong-secret") is False


def test_verify_rejects_missing_signature():
    body = _signed_body()
    del body["Signature"]
    assert verify_callback(body, "sk") is False


def test_missing_fields_treated_as_empty_string():
    # 缺字段按空串拼接：与显式空串等价
    sparse = {"AppId": "app1", "Nonce": "1", "Signature": None}
    full = {k: "" for k in SIGN_FIELDS}
    full.update({"AppId": "app1", "Nonce": "1"})
    assert compute_signature(sparse, "sk") == compute_signature(full, "sk")
    assert len(SIGN_FIELDS) == 7
