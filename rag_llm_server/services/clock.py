"""与具体存储实现无关的时间工具。"""
from datetime import datetime, timezone


def utc_now() -> str:
    """返回 ISO-8601 UTC 时间串。"""
    return datetime.now(timezone.utc).isoformat()
