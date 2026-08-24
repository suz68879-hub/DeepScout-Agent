"""RTC 回调验签（火山官方算法：字段名排序 + SHA256，非 HMAC）。

签名在 JSON Body 的 Signature 字段；密钥 RTC_CALLBACK_SECRET 不配置则跳过校验。
字段缺省按空串参与拼接（官方文档行为）。
"""
import hashlib

SIGN_FIELDS = ["EventType", "EventData", "EventTime", "EventId", "Version", "AppId", "Nonce"]


def compute_signature(body: dict, secret: str) -> str:
    """按字段名字母序拼接值（含 SecretKey）后 SHA256 十六进制。"""
    params = {k: str(body.get(k, "")) for k in SIGN_FIELDS}
    params["SecretKey"] = secret
    payload = "".join(params[k] for k in sorted(params))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_callback(body: dict, secret: str) -> bool:
    """校验 body 中的 Signature 字段；不一致返回 False。"""
    return compute_signature(body, secret) == body.get("Signature", "")
