import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.storage.sqlite import _SCHEMA

NAMESPACE = uuid.UUID("4df4ff97-18dc-4e7f-9cc2-255408bbcda6")


def _id(kind: str, number: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{kind}:{number}"))


def generate_fixture(output: Path | str, users: int = 25) -> dict[str, int]:
    if users < 1:
        raise ValueError("users must be positive")
    path = Path(output)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing fixture: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = {
        "app_user": users,
        "auth_session": users,
        "resume": users,
        "interview_session": users * 2,
        "message": users * 8,
        "interview_report": users * 2,
        "recording": users * 2,
    }
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(_SCHEMA)
        message_id = 1
        for user_number in range(1, users + 1):
            user_id = _id("user", user_number)
            resume_id = _id("resume", user_number)
            connection.execute(
                "INSERT INTO app_user VALUES (?, ?, ?, ?, ?)",
                (
                    user_id,
                    f"synthetic_user_{user_number:04d}",
                    "synthetic_not_a_real_password_hash",
                    "user",
                    "2026-01-01T00:00:00Z",
                ),
            )
            connection.execute(
                "INSERT INTO auth_session VALUES (?, ?, ?, ?, ?)",
                (
                    hashlib.sha256(f"synthetic-token-{user_number}".encode()).hexdigest(),
                    user_id,
                    "2026-01-01T00:00:00Z",
                    "2026-01-02T00:00:00Z",
                    None,
                ),
            )
            connection.execute(
                "INSERT INTO resume VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resume_id,
                    user_id,
                    "synthetic resume content",
                    '{"skills":["synthetic-python"]}',
                    "fixture",
                    "ready",
                    "2026-01-01T00:01:00Z",
                    "2026-01-01T00:01:00Z",
                ),
            )
            for attempt in range(1, 3):
                ordinal = (user_number - 1) * 2 + attempt
                session_id = _id("session", ordinal)
                report_id = _id("report", ordinal)
                recording_id = _id("recording", ordinal)
                connection.execute(
                    "INSERT INTO interview_session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        user_id,
                        resume_id,
                        "synthetic engineer",
                        "technical",
                        "finished",
                        "2026-01-01T00:02:00Z",
                        "2026-01-01T00:03:00Z",
                        f"synthetic-room-{ordinal}",
                        f"synthetic-rtc-user-{ordinal}",
                        f"synthetic-task-{ordinal}",
                        f"synthetic-callback-{ordinal}",
                        "finished",
                        0,
                        1,
                    ),
                )
                for seq in range(1, 5):
                    connection.execute(
                        "INSERT INTO message VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            message_id,
                            session_id,
                            "assistant" if seq % 2 else "user",
                            f"synthetic message {seq}",
                            seq,
                            f"2026-01-01T00:02:{seq:02d}Z",
                        ),
                    )
                    message_id += 1
                connection.execute(
                    "INSERT INTO interview_report VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        report_id,
                        user_id,
                        session_id,
                        '{"total":90}',
                        '{"summary":"synthetic"}',
                        '["synthetic practice"]',
                        "synthetic engineer",
                        "session",
                        f"reports/synthetic-{ordinal}.md",
                        "2026-01-01T00:04:00Z",
                    ),
                )
                connection.execute(
                    "INSERT INTO recording VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        recording_id,
                        user_id,
                        f"synthetic-{ordinal}.wav",
                        "wav",
                        "synthetic engineer",
                        f"recordings/synthetic-{ordinal}.wav",
                        16,
                        "finished",
                        None,
                        '{"text":"synthetic transcript"}',
                        None,
                        report_id,
                        "2026-01-01T00:05:00Z",
                        "2026-01-01T00:06:00Z",
                    ),
                )
        connection.commit()
    return counts


def _main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic no-PII migration fixture")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--users", type=int, default=25)
    args = parser.parse_args()
    counts = generate_fixture(args.output, args.users)
    print(json.dumps({"counts": counts, "total_rows": sum(counts.values())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
