import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from db.models import (
    AppUser,
    AuthSession,
    InterviewReport,
    InterviewSession,
    Message,
    Recording,
    Resume,
)


@dataclass(frozen=True)
class TableSpec:
    name: str
    table: Any
    primary_key: str
    uuid_columns: frozenset[str] = frozenset()
    json_columns: frozenset[str] = frozenset()
    datetime_columns: frozenset[str] = frozenset()


TABLE_SPECS = (
    TableSpec(
        "app_user",
        AppUser.__table__,
        "id",
        frozenset({"id"}),
        datetime_columns=frozenset({"created_at"}),
    ),
    TableSpec(
        "auth_session",
        AuthSession.__table__,
        "token_hash",
        frozenset({"user_id"}),
        datetime_columns=frozenset({"created_at", "expires_at", "revoked_at"}),
    ),
    TableSpec(
        "resume",
        Resume.__table__,
        "id",
        frozenset({"id", "user_id"}),
        frozenset({"structured_json"}),
        frozenset({"created_at", "updated_at"}),
    ),
    TableSpec(
        "interview_session",
        InterviewSession.__table__,
        "id",
        frozenset({"id", "user_id", "resume_id"}),
        datetime_columns=frozenset({"started_at", "ended_at"}),
    ),
    TableSpec(
        "message",
        Message.__table__,
        "id",
        frozenset({"session_id"}),
        datetime_columns=frozenset({"created_at"}),
    ),
    TableSpec(
        "interview_report",
        InterviewReport.__table__,
        "id",
        frozenset({"id", "user_id", "session_id"}),
        frozenset({"scores_json", "feedback_json", "suggestions_json"}),
        frozenset({"created_at"}),
    ),
    TableSpec(
        "recording",
        Recording.__table__,
        "id",
        frozenset({"id", "user_id", "report_id"}),
        frozenset({"transcript_json"}),
        frozenset({"created_at", "finished_at"}),
    ),
)


def normalize_row(spec: TableSpec, row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for column in spec.uuid_columns:
        if normalized.get(column) is not None:
            normalized[column] = uuid.UUID(str(normalized[column]))
    for column in spec.json_columns:
        value = normalized.get(column)
        if isinstance(value, str):
            normalized[column] = json.loads(value)
    for column in spec.datetime_columns:
        value = normalized.get(column)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            normalized[column] = (
                parsed.replace(tzinfo=UTC)
                if parsed.tzinfo is None
                else parsed.astimezone(UTC)
            )
    return normalized


def encode_resume_token(table: str, primary_key: Any) -> str:
    payload = json.dumps(
        {"table": table, "pk": str(primary_key)}, separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_resume_token(token: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload = json.loads(raw)
        table = payload["table"]
        primary_key = payload["pk"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid resume-from token") from exc
    if table not in {spec.name for spec in TABLE_SPECS} or not isinstance(
        primary_key, str
    ):
        raise ValueError("invalid resume-from token")
    return table, primary_key
