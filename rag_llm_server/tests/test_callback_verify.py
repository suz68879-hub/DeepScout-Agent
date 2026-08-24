"""回调验签：排序拼接、字段缺省与防篡改。"""
import hashlib

from services.callback_verify import SIGN_FIELDS, compute_signature, verify_callback


def _signed_body(secret="sk", **over):
    body = {
        "AppId": "app1", "EventData": "d", "EventId": "e", "EventTime": "t",
        "EventType": "v", "Nonce": "1234", "Version": "2020-12-01",
    }
    body.update(over)
    body["Signature"] = compute_signature(body, secret)
    return body


def test_compute_signature_is_sorted_field_concat_sha256():
    # 手工构造期望值：字母序 [AppId, EventData, EventId, EventTime, EventType, Nonce, SecretKey, Version]
    payload = "app1" + "d" + "e" + "t" + "v" + "1234" + "sk" + "2020-12-01"
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    body = {k: v for k, v in {
        "AppId": "app1", "EventData": "d", "EventId": "e", "EventTime": "t",
        "EventType": "v", "Nonce": "1234", "Version": "2020-12-01",
    }.items()}
    assert compute_signature(body, "sk") == expected


def test_verify_accepts_valid_signature():
    assert verify_callback(_signed_body(), "sk") is True


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
