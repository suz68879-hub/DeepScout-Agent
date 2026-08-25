import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime


class CursorError(ValueError):
    pass


@dataclass(frozen=True)
class Cursor:
    created_at: str
    id: str


@dataclass(frozen=True)
class Page:
    items: list[dict]
    next_cursor: str | None


def encode_cursor(created_at: str, row_id: str) -> str:
    payload = json.dumps(
        {"v": 1, "created_at": created_at, "id": row_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> Cursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if set(payload) != {"v", "created_at", "id"} or payload["v"] != 1:
            raise ValueError
        parsed_created_at = datetime.fromisoformat(payload["created_at"])
        if parsed_created_at.tzinfo is None:
            raise ValueError
        row_id = str(uuid.UUID(payload["id"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise CursorError("invalid or expired cursor") from None
    return Cursor(created_at=payload["created_at"], id=row_id)
