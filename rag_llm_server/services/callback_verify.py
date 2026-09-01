"""RTC 回调验签：官方字段排序 SHA256，并绑定 messages、时间窗与重放 ID。"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

SIGN_FIELDS = ["EventType", "EventData", "EventTime", "EventId", "Version", "AppId", "Nonce"]
CALLBACK_MAX_SKEW_SECONDS = 300


def canonicalize_messages(messages) -> str:
    """将 messages 规范成稳定 JSON，供签名绑定口吻内容。"""
    if messages is None:
        messages = []
    try:
        return json.dumps(
            messages, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
    except (TypeError, ValueError):
        return ""


def _field_value(body: dict, key: str) -> str:
    value = body.get(key, "")
    if value is None:
        return ""
    return str(value)


def compute_signature(body: dict, secret: str) -> str:
    """按字段名字母序拼接值（含 SecretKey；若有 messages 则绑定其规范 JSON）后 SHA256。

    无 messages 键时与火山房间事件 7 字段算法一致；带 messages 时把口吻内容纳入签名，
    防止只改 messages 而沿用原 Signature。
    """
    params = {key: _field_value(body, key) for key in SIGN_FIELDS}
    params["SecretKey"] = secret
    if "messages" in body:
        params["Messages"] = canonicalize_messages(body.get("messages"))
    payload = "".join(params[key] for key in sorted(params))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def callback_replay_id(body: dict) -> str:
    """重放窗口主键：优先 EventId，否则 Nonce。"""
    event_id = _field_value(body, "EventId").strip()
    if event_id:
        return event_id
    return _field_value(body, "Nonce").strip()


def _parse_event_time(raw) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def verify_callback(body: dict, secret: str) -> bool:
    """校验 Signature、messages 绑定、时间窗与重放 ID；不一致返回 False。"""
    provided = body.get("Signature", "")
    if not isinstance(provided, str) or not provided:
        return False
    expected = compute_signature(body, secret)
    if len(provided) != len(expected) or not hmac.compare_digest(expected, provided):
        return False
    if not callback_replay_id(body):
        return False
    event_time = _parse_event_time(body.get("EventTime"))
    if event_time is None:
        return False
    return abs(time.time() - event_time) <= CALLBACK_MAX_SKEW_SECONDS
