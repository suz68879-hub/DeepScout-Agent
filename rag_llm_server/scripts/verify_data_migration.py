import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migration_common import TABLE_SPECS, TableSpec, normalize_row


@dataclass(frozen=True)
class TableVerification:
    source_count: int
    target_count: int
    source_pk_hash: str
    target_pk_hash: str
    source_row_hash: str
    target_row_hash: str
    source_object_key_hash: str
    target_object_key_hash: str

    @property
    def ok(self) -> bool:
        return (
            self.source_count == self.target_count
            and self.source_pk_hash == self.target_pk_hash
            and self.source_row_hash == self.target_row_hash
            and self.source_object_key_hash == self.target_object_key_hash
        )


@dataclass(frozen=True)
class VerificationResult:
    tables: dict[str, TableVerification]
    source_fk_errors: int
    target_fk_errors: int
    source_owner_errors: int
    target_owner_errors: int

    @property
    def ok(self) -> bool:
        return (
            all(table.ok for table in self.tables.values())
            and self.source_fk_errors == 0
            and self.target_fk_errors == 0
            and self.source_owner_errors == 0
            and self.target_owner_errors == 0
        )

    def safe_report(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tables": {
                name: {"ok": value.ok, **asdict(value)}
                for name, value in self.tables.items()
            },
            "source_fk_errors": self.source_fk_errors,
            "target_fk_errors": self.target_fk_errors,
            "source_owner_errors": self.source_owner_errors,
            "target_owner_errors": self.target_owner_errors,
        }


def _canonical(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _digest(values: list[Any]) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = json.dumps(
            _canonical(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest.update(encoded)
        digest.update(b"\n")
    return digest.hexdigest()


def _table_metrics(
    spec: TableSpec,
    source_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
) -> TableVerification:
    object_key_column = {
        "interview_report": "md_path",
        "recording": "tos_key",
    }.get(spec.name)

    def keys(rows: list[dict[str, Any]]) -> list[Any]:
        if object_key_column is None:
            return []
        return sorted(
            row[object_key_column]
            for row in rows
            if row.get(object_key_column) is not None
        )

    return TableVerification(
        source_count=len(source_rows),
        target_count=len(target_rows),
        source_pk_hash=_digest([row[spec.primary_key] for row in source_rows]),
        target_pk_hash=_digest([row[spec.primary_key] for row in target_rows]),
        source_row_hash=_digest(source_rows),
        target_row_hash=_digest(target_rows),
        source_object_key_hash=_digest(keys(source_rows)),
        target_object_key_hash=_digest(keys(target_rows)),
    )


def _integrity_errors(rows: dict[str, list[dict[str, Any]]]) -> tuple[int, int]:
    users = {str(row["id"]): row for row in rows["app_user"]}
    resumes = {str(row["id"]): row for row in rows["resume"]}
    sessions = {str(row["id"]): row for row in rows["interview_session"]}
    reports = {str(row["id"]): row for row in rows["interview_report"]}
    fk_errors = 0
    owner_errors = 0

    for row in rows["auth_session"]:
        fk_errors += str(row["user_id"]) not in users
    for row in rows["resume"]:
        fk_errors += str(row["user_id"]) not in users
    for row in rows["interview_session"]:
        user_id = str(row["user_id"])
        fk_errors += user_id not in users
        if row.get("resume_id") is not None:
            resume = resumes.get(str(row["resume_id"]))
            fk_errors += resume is None
            owner_errors += resume is not None and str(resume["user_id"]) != user_id
    for row in rows["message"]:
        fk_errors += str(row["session_id"]) not in sessions
    for row in rows["interview_report"]:
        user_id = str(row["user_id"])
        fk_errors += user_id not in users
        if row.get("session_id") is not None:
            session = sessions.get(str(row["session_id"]))
            fk_errors += session is None
            owner_errors += session is not None and str(session["user_id"]) != user_id
    for row in rows["recording"]:
        user_id = str(row["user_id"])
        fk_errors += user_id not in users
        if row.get("report_id") is not None:
            report = reports.get(str(row["report_id"]))
            fk_errors += report is None
            owner_errors += report is not None and str(report["user_id"]) != user_id
    return int(fk_errors), int(owner_errors)


def _load_source(source: Path) -> dict[str, list[dict[str, Any]]]:
    resolved = source.resolve(strict=True)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return {
            spec.name: [
                normalize_row(spec, dict(row))
                for row in connection.execute(
                    f'SELECT * FROM "{spec.name}" ORDER BY "{spec.primary_key}"'
                ).fetchall()
            ]
            for spec in TABLE_SPECS
        }
    finally:
        connection.close()


async def _load_target(target: str) -> dict[str, list[dict[str, Any]]]:
    engine = create_async_engine(target)
    try:
        async with engine.connect() as connection:
            result = {}
            for spec in TABLE_SPECS:
                rows = await connection.execute(
                    select(spec.table).order_by(spec.table.c[spec.primary_key])
                )
                result[spec.name] = [dict(row) for row in rows.mappings()]
            return result
    finally:
        await engine.dispose()


async def verify_database(
    source: Path | str, target: str
) -> VerificationResult:
    source_rows = _load_source(Path(source))
    target_rows = await _load_target(target)
    tables = {
        spec.name: _table_metrics(
            spec, source_rows[spec.name], target_rows[spec.name]
        )
        for spec in TABLE_SPECS
    }
    source_fk, source_owner = _integrity_errors(source_rows)
    target_fk, target_owner = _integrity_errors(target_rows)
    return VerificationResult(
        tables,
        source_fk,
        target_fk,
        source_owner,
        target_owner,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a complete SQLite to PostgreSQL migration"
    )
    parser.add_argument("--source", required=True, type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--target")
    target.add_argument("--target-env")
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    target = args.target
    if args.target_env:
        target = os.getenv(args.target_env)
        if not target:
            print(json.dumps({"error": "target environment variable is missing"}))
            return 1
    try:
        result = await verify_database(args.source, target)
    except Exception:
        print(json.dumps({"error": "verification failed; inspect sanitized service logs"}))
        return 1
    print(json.dumps(result.safe_report(), sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    raise SystemExit(asyncio.run(_main(), loop_factory=loop_factory))
