import argparse
import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.migration_common import (
    TABLE_SPECS,
    TableSpec,
    decode_resume_token,
    encode_resume_token,
    normalize_row,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationResult:
    counts: dict[str, int]
    completed: bool
    next_resume_from: str | None

    @property
    def total_rows(self) -> int:
        return sum(self.counts.values())


def _open_source(source: Path) -> sqlite3.Connection:
    resolved = source.resolve(strict=True)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


async def _write_batch(
    engine: AsyncEngine, spec: TableSpec, rows: list[dict]
) -> None:
    statement = insert(spec.table).values(rows)
    updates = {
        column.name: statement.excluded[column.name]
        for column in spec.table.columns
        if column.name != spec.primary_key
    }
    statement = statement.on_conflict_do_update(
        index_elements=[spec.table.c[spec.primary_key]],
        set_=updates,
    )
    async with engine.begin() as connection:
        await connection.execute(statement)


async def _sync_message_sequence(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('message', 'id'), "
                "COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM message"
            )
        )


async def migrate_database(
    source: Path | str,
    target: str,
    *,
    batch_size: int = 500,
    dry_run: bool = False,
    resume_from: str | None = None,
    stop_after_batches: int | None = None,
) -> MigrationResult:
    if batch_size < 1:
        raise ValueError("batch-size must be positive")
    if stop_after_batches is not None and stop_after_batches < 1:
        raise ValueError("stop-after-batches must be positive")
    resume_table, resume_pk = (
        decode_resume_token(resume_from) if resume_from else (None, None)
    )
    source_connection = _open_source(Path(source))
    engine = None if dry_run else create_async_engine(target)
    counts = {spec.name: 0 for spec in TABLE_SPECS}
    last_token = resume_from
    committed_batches = 0
    resume_reached = resume_table is None
    try:
        for spec in TABLE_SPECS:
            if not resume_reached:
                if spec.name != resume_table:
                    continue
                resume_reached = True
            query = f'SELECT * FROM "{spec.name}"'
            parameters: tuple[str, ...] = ()
            if spec.name == resume_table:
                query += f' WHERE "{spec.primary_key}" > ?'
                parameters = (resume_pk,)
            query += f' ORDER BY "{spec.primary_key}"'
            cursor = source_connection.execute(query, parameters)
            while batch := cursor.fetchmany(batch_size):
                rows = [normalize_row(spec, dict(row)) for row in batch]
                try:
                    if engine is not None:
                        await _write_batch(engine, spec, rows)
                except Exception as exc:
                    key_hash = hashlib.sha256(
                        str(batch[0][spec.primary_key]).encode()
                    ).hexdigest()[:12]
                    logger.error(
                        "migration batch failed",
                        extra={
                            "table": spec.name,
                            "primary_key_hash": key_hash,
                        },
                    )
                    raise RuntimeError(
                        "migration batch failed: "
                        f"table={spec.name}, primary_key_hash={key_hash}"
                    ) from exc
                counts[spec.name] += len(rows)
                last_token = encode_resume_token(
                    spec.name, batch[-1][spec.primary_key]
                )
                committed_batches += 1
                if stop_after_batches == committed_batches:
                    return MigrationResult(counts, False, last_token)
        if engine is not None:
            await _sync_message_sequence(engine)
        return MigrationResult(counts, True, None)
    finally:
        source_connection.close()
        if engine is not None:
            await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate SQLite business data to PostgreSQL"
    )
    parser.add_argument("--source", required=True, type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--target")
    target.add_argument("--target-env")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume-from")
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
        result = await migrate_database(
            args.source,
            target,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            resume_from=args.resume_from,
        )
    except Exception:
        print(json.dumps({"error": "migration failed; inspect sanitized service logs"}))
        return 1
    print(
        json.dumps(
            {
                "counts": result.counts,
                "completed": result.completed,
                "next_resume_from": result.next_resume_from,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    raise SystemExit(asyncio.run(_main(), loop_factory=loop_factory))
