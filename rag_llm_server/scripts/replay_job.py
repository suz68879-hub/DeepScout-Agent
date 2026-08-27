"""Controlled CLI for creating an audited replay of one failed job."""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from config import Config
from db.engine import build_database_runtime
from services.jobs.replay import JobReplayService, ReplayError
from services.jobs.types import JobStatus


class ReplayCliError(RuntimeError):
    pass


def validate_execution_guard(*, app_env: str, confirm_production: bool) -> None:
    if app_env == "production" and not confirm_production:
        raise ReplayCliError("PRODUCTION_CONFIRMATION_REQUIRED")


def replay_output(
    *,
    source_job_id: uuid.UUID,
    replay_job_id: uuid.UUID | None,
    status: JobStatus | str,
    dry_run: bool,
) -> dict[str, str | bool | None]:
    return {
        "source_job_id": str(source_job_id),
        "replay_job_id": str(replay_job_id) if replay_job_id else None,
        "status": status.value if isinstance(status, JobStatus) else status,
        "dry_run": dry_run,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay one failed background job")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--approved-by")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-production", action="store_true")
    return parser


async def _run(args: argparse.Namespace, config: Config) -> dict:
    validate_execution_guard(
        app_env=config.APP_ENV,
        confirm_production=args.confirm_production,
    )
    if config.STORAGE_BACKEND != "postgres":
        raise ReplayCliError("POSTGRES_REQUIRED")
    runtime = build_database_runtime(config)
    await runtime.start()
    try:
        async with runtime.session_scope() as session:
            outcome = await JobReplayService(
                session,
                app_env=config.APP_ENV,
            ).replay(
                job_id=args.job_id,
                operator=args.operator,
                reason=args.reason,
                approved_by=args.approved_by,
                dry_run=args.dry_run,
            )
        replay_job = outcome.replay_job
        return replay_output(
            source_job_id=outcome.source_job_id,
            replay_job_id=replay_job.id if replay_job else None,
            status=replay_job.status if replay_job else "validated",
            dry_run=outcome.dry_run,
        )
    finally:
        await runtime.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = Config()
        if os.name == "nt":
            result = asyncio.run(
                _run(args, config),
                loop_factory=asyncio.SelectorEventLoop,
            )
        else:
            result = asyncio.run(_run(args, config))
    except ReplayError as exc:
        print(json.dumps({"error_code": exc.code.value}), file=sys.stderr)
        return 2
    except ReplayCliError as exc:
        print(json.dumps({"error_code": str(exc)}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"error_code": "REPLAY_FAILED"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
