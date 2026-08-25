"""Async SQLite repository with user ownership and idempotent legacy migration."""
import sqlite3
import secrets
import uuid
from pathlib import Path

import aiosqlite

from config import settings
from services.auth_service import hash_password_async, normalize_username, validate_password
from services.clock import utc_now
from .base import BaseStorage, StorageConflictError


_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_user (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL COLLATE NOCASE UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_session (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_user(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS resume (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_user(id),
  content TEXT,
  structured_json TEXT,
  source TEXT,
  status TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS interview_session (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_user(id),
  resume_id TEXT REFERENCES resume(id),
  position TEXT,
  stage TEXT,
  status TEXT,
  started_at TEXT, ended_at TEXT,
  rtc_room_id TEXT NOT NULL UNIQUE,
  rtc_user_id TEXT NOT NULL UNIQUE,
  rtc_task_id TEXT NOT NULL UNIQUE,
  rtc_callback_id TEXT NOT NULL UNIQUE,
  rtc_status TEXT NOT NULL DEFAULT 'created'
);
CREATE TABLE IF NOT EXISTS message (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES interview_session(id),
  role TEXT,
  content TEXT,
  seq INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS interview_report (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_user(id),
  session_id TEXT REFERENCES interview_session(id),
  scores_json TEXT,
  feedback_json TEXT,
  suggestions_json TEXT,
  position TEXT,
  source TEXT,
  md_path TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS recording (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_user(id),
  filename TEXT,
  ext TEXT,
  tos_key TEXT,
  size_bytes INTEGER,
  status TEXT,
  asr_task_id TEXT,
  transcript_json TEXT,
  error TEXT,
  report_id TEXT REFERENCES interview_report(id),
  created_at TEXT,
  finished_at TEXT
);
"""

_OWNER_TABLES = ("resume", "interview_session", "interview_report", "recording")


class SqliteStorage(BaseStorage):
    RESUME_COLS = {"content", "structured_json", "source", "status"}
    SESSION_COLS = {"resume_id", "position", "stage", "status", "ended_at", "rtc_status"}
    RECORDING_COLS = {
        "filename", "ext", "tos_key", "size_bytes", "status", "asr_task_id",
        "transcript_json", "error", "report_id", "finished_at",
    }

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._conn is not None:
            return
        Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(_SCHEMA)
        await self._migrate_legacy_schema()

    async def _columns(self, table: str) -> set[str]:
        rows = await (await self._c().execute(f"PRAGMA table_info({table})")).fetchall()
        return {row[1] for row in rows}

    async def _add_column(self, table: str, name: str, definition: str) -> None:
        if name not in await self._columns(table):
            await self._c().execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    async def _migrate_legacy_schema(self) -> None:
        conn = self._c()
        await conn.execute("BEGIN IMMEDIATE")
        try:
            await self._add_column("interview_report", "position", "TEXT")
            await self._add_column("interview_report", "source", "TEXT DEFAULT 'session'")
            for table in _OWNER_TABLES:
                await self._add_column(table, "user_id", "TEXT REFERENCES app_user(id)")
            await self._add_column("interview_session", "rtc_room_id", "TEXT")
            await self._add_column("interview_session", "rtc_user_id", "TEXT")
            await self._add_column("interview_session", "rtc_task_id", "TEXT")
            await self._add_column("interview_session", "rtc_callback_id", "TEXT")
            await self._add_column(
                "interview_session", "rtc_status", "TEXT NOT NULL DEFAULT 'created'",
            )

            session_rows = await (
                await conn.execute(
                    "SELECT id FROM interview_session WHERE rtc_room_id IS NULL "
                    "OR rtc_user_id IS NULL OR rtc_task_id IS NULL OR rtc_callback_id IS NULL"
                )
            ).fetchall()
            for row in session_rows:
                await conn.execute(
                    "UPDATE interview_session SET rtc_room_id=?, rtc_user_id=?, rtc_task_id=?, "
                    "rtc_callback_id=?, rtc_status=COALESCE(rtc_status, 'created') WHERE id=?",
                    (*self._new_rtc_ids(), row["id"]),
                )

            unowned = 0
            for table in _OWNER_TABLES:
                row = await (await conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL"
                )).fetchone()
                unowned += int(row[0])
            if unowned:
                user_id = await self._ensure_bootstrap_admin()
                for table in _OWNER_TABLES:
                    await conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (user_id,),
                    )

            await conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_auth_session_user ON auth_session(user_id, expires_at);
            CREATE INDEX IF NOT EXISTS idx_resume_user_created ON resume(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_session_user_status ON interview_session(user_id, status, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_report_user_created ON interview_report(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_recording_user_created ON recording(user_id, created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_session_rtc_room ON interview_session(rtc_room_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_session_rtc_user ON interview_session(rtc_user_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_session_rtc_task ON interview_session(rtc_task_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_session_rtc_callback ON interview_session(rtc_callback_id);
            """)
            for table in _OWNER_TABLES:
                row = await (await conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL"
                )).fetchone()
                if row[0]:
                    raise RuntimeError(f"ownership migration incomplete for {table}")
            await self._audit_ownership()
            await conn.execute("PRAGMA user_version = 2")
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def _audit_ownership(self) -> None:
        checks = {
            "orphaned owner": """
                SELECT 1 FROM (
                    SELECT user_id FROM resume UNION ALL
                    SELECT user_id FROM interview_session UNION ALL
                    SELECT user_id FROM interview_report UNION ALL
                    SELECT user_id FROM recording
                ) owned
                WHERE NOT EXISTS (SELECT 1 FROM app_user u WHERE u.id = owned.user_id)
                LIMIT 1
            """,
            "session/resume ownership mismatch": """
                SELECT 1 FROM interview_session s
                LEFT JOIN resume r ON r.id = s.resume_id
                WHERE s.resume_id IS NOT NULL AND (r.id IS NULL OR r.user_id <> s.user_id)
                LIMIT 1
            """,
            "report/session ownership mismatch": """
                SELECT 1 FROM interview_report r
                LEFT JOIN interview_session s ON s.id = r.session_id
                WHERE r.session_id IS NOT NULL AND (s.id IS NULL OR s.user_id <> r.user_id)
                LIMIT 1
            """,
            "recording/report ownership mismatch": """
                SELECT 1 FROM recording r
                LEFT JOIN interview_report p ON p.id = r.report_id
                WHERE r.report_id IS NOT NULL AND (p.id IS NULL OR p.user_id <> r.user_id)
                LIMIT 1
            """,
            "orphaned message": """
                SELECT 1 FROM message m
                LEFT JOIN interview_session s ON s.id = m.session_id
                WHERE m.session_id IS NULL OR s.id IS NULL
                LIMIT 1
            """,
        }
        for label, sql in checks.items():
            if await (await self._c().execute(sql)).fetchone():
                raise RuntimeError(f"database ownership audit failed: {label}")

    async def _ensure_bootstrap_admin(self) -> str:
        username_raw = settings.BOOTSTRAP_ADMIN_USERNAME
        password = settings.BOOTSTRAP_ADMIN_PASSWORD
        if not username_raw or not password:
            raise RuntimeError(
                "legacy data requires BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD"
            )
        try:
            username = normalize_username(username_raw)
            validate_password(password)
        except ValueError as exc:
            raise RuntimeError(f"invalid BOOTSTRAP_ADMIN credentials: {exc}") from exc
        row = await (await self._c().execute(
            "SELECT id FROM app_user WHERE username = ?", (username,),
        )).fetchone()
        if row:
            return row["id"]
        user_id = str(uuid.uuid4())
        password_hash = await hash_password_async(password)
        await self._c().execute(
            "INSERT INTO app_user (id, username, password_hash, role, created_at) "
            "VALUES (?, ?, ?, 'admin', ?)",
            (user_id, username, password_hash, utc_now()),
        )
        return user_id

    @staticmethod
    def _new_rtc_ids() -> tuple[str, str, str, str]:
        return (
            f"room_{uuid.uuid4().hex}",
            f"user_{uuid.uuid4().hex}",
            f"task_{uuid.uuid4().hex}",
            secrets.token_urlsafe(24),
        )

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    def _c(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("storage is not initialized")
        return self._conn

    # ---------- auth ----------

    async def user_create(self, username: str, password_hash: str, role: str = "user") -> dict:
        row = {
            "id": str(uuid.uuid4()), "username": username, "password_hash": password_hash,
            "role": role, "created_at": utc_now(),
        }
        try:
            await self._c().execute(
                "INSERT INTO app_user (id, username, password_hash, role, created_at) "
                "VALUES (:id, :username, :password_hash, :role, :created_at)", row,
            )
            await self._c().commit()
        except sqlite3.IntegrityError:
            await self._c().rollback()
            raise StorageConflictError("username already exists") from None
        return row

    async def user_get_by_username(self, username: str) -> dict | None:
        row = await (await self._c().execute(
            "SELECT * FROM app_user WHERE username = ?", (username,),
        )).fetchone()
        return dict(row) if row else None

    async def auth_session_create(self, user_id: str, token_hash: str, expires_at: str) -> None:
        await self._c().execute(
            "INSERT INTO auth_session (token_hash, user_id, created_at, expires_at, revoked_at) "
            "VALUES (?, ?, ?, ?, NULL)", (token_hash, user_id, utc_now(), expires_at),
        )
        await self._c().commit()

    async def auth_session_get_user(self, token_hash: str) -> dict | None:
        row = await (await self._c().execute(
            "SELECT u.* FROM auth_session s JOIN app_user u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
            (token_hash, utc_now()),
        )).fetchone()
        return dict(row) if row else None

    async def auth_session_revoke(self, token_hash: str) -> None:
        await self._c().execute(
            "UPDATE auth_session SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (utc_now(), token_hash),
        )
        await self._c().commit()

    async def auth_session_cleanup(self) -> None:
        await self._c().execute(
            "DELETE FROM auth_session WHERE expires_at <= ? OR revoked_at IS NOT NULL", (utc_now(),),
        )
        await self._c().commit()

    # ---------- owned helpers ----------

    async def _owned_get(self, table: str, user_id: str, row_id: str) -> dict | None:
        row = await (await self._c().execute(
            f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", (row_id, user_id),
        )).fetchone()
        return dict(row) if row else None

    # ---------- resume ----------

    async def resume_create(self, user_id: str, resume: dict) -> dict:
        row = dict(resume)
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("structured_json", None)
        row.setdefault("created_at", utc_now())
        row.setdefault("updated_at", utc_now())
        row["user_id"] = user_id
        await self._c().execute(
            "INSERT INTO resume (id, user_id, content, structured_json, source, status, created_at, updated_at) "
            "VALUES (:id, :user_id, :content, :structured_json, :source, :status, :created_at, :updated_at)",
            row,
        )
        await self._c().commit()
        return row

    async def resume_get(self, user_id: str, resume_id: str) -> dict | None:
        return await self._owned_get("resume", user_id, resume_id)

    async def resume_update(self, user_id: str, resume_id: str, patch: dict) -> dict | None:
        fields = {key: patch[key] for key in patch if key in self.RESUME_COLS}
        if not fields:
            return await self.resume_get(user_id, resume_id)
        fields["updated_at"] = utc_now()
        sets = ", ".join(f"{key} = :{key}" for key in fields)
        await self._c().execute(
            f"UPDATE resume SET {sets} WHERE id = :id AND user_id = :user_id",
            {**fields, "id": resume_id, "user_id": user_id},
        )
        await self._c().commit()
        return await self.resume_get(user_id, resume_id)

    async def resume_list(self, user_id: str) -> list[dict]:
        rows = await (await self._c().execute(
            "SELECT * FROM resume WHERE user_id = ? ORDER BY created_at ASC", (user_id,),
        )).fetchall()
        return [dict(row) for row in rows]

    async def resume_latest(self, user_id: str) -> dict | None:
        row = await (await self._c().execute(
            "SELECT * FROM resume WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,),
        )).fetchone()
        return dict(row) if row else None

    # ---------- interview session ----------

    async def session_create(self, user_id: str, session: dict) -> dict:
        row = dict(session)
        if row.get("resume_id") and not await self.resume_get(user_id, row["resume_id"]):
            raise ValueError("resume does not belong to user")
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("started_at", None)
        row.setdefault("ended_at", None)
        room_id, rtc_user_id, task_id, callback_id = self._new_rtc_ids()
        row.setdefault("rtc_room_id", room_id)
        row.setdefault("rtc_user_id", rtc_user_id)
        row.setdefault("rtc_task_id", task_id)
        row.setdefault("rtc_callback_id", callback_id)
        row.setdefault("rtc_status", "created")
        row["user_id"] = user_id
        await self._c().execute(
            "INSERT INTO interview_session (id, user_id, resume_id, position, stage, status, "
            "started_at, ended_at, rtc_room_id, rtc_user_id, rtc_task_id, rtc_callback_id, rtc_status) "
            "VALUES (:id, :user_id, :resume_id, :position, :stage, :status, :started_at, :ended_at, "
            ":rtc_room_id, :rtc_user_id, :rtc_task_id, :rtc_callback_id, :rtc_status)", row,
        )
        await self._c().commit()
        return row

    async def session_get(self, user_id: str, session_id: str) -> dict | None:
        return await self._owned_get("interview_session", user_id, session_id)

    async def session_get_internal(self, session_id: str) -> dict | None:
        row = await (await self._c().execute(
            "SELECT * FROM interview_session WHERE id = ?", (session_id,),
        )).fetchone()
        return dict(row) if row else None

    async def session_get_by_callback(self, callback_id: str) -> dict | None:
        row = await (await self._c().execute(
            "SELECT * FROM interview_session WHERE rtc_callback_id = ?", (callback_id,),
        )).fetchone()
        return dict(row) if row else None

    async def session_update(self, user_id: str, session_id: str, patch: dict) -> dict | None:
        fields = {key: patch[key] for key in patch if key in self.SESSION_COLS}
        if not fields:
            return await self.session_get(user_id, session_id)
        sets = ", ".join(f"{key} = :{key}" for key in fields)
        await self._c().execute(
            f"UPDATE interview_session SET {sets} WHERE id = :id AND user_id = :user_id",
            {**fields, "id": session_id, "user_id": user_id},
        )
        await self._c().commit()
        return await self.session_get(user_id, session_id)

    async def session_list_running(self, user_id: str) -> list[dict]:
        rows = await (await self._c().execute(
            "SELECT * FROM interview_session WHERE user_id = ? AND status = 'running' "
            "ORDER BY started_at DESC", (user_id,),
        )).fetchall()
        return [dict(row) for row in rows]

    # ---------- message ----------

    async def message_append(self, user_id: str, session_id: str, role: str, content: str) -> dict:
        if not await self.session_get(user_id, session_id):
            raise ValueError("session does not belong to user")
        seq = (await (await self._c().execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM message WHERE session_id = ?", (session_id,),
        )).fetchone())[0]
        cur = await self._c().execute(
            "INSERT INTO message (session_id, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, seq, utc_now()),
        )
        await self._c().commit()
        row = await (await self._c().execute(
            "SELECT * FROM message WHERE id = ?", (cur.lastrowid,),
        )).fetchone()
        return dict(row)

    async def message_list(self, user_id: str, session_id: str, limit: int = 100) -> list[dict]:
        rows = await (await self._c().execute(
            "SELECT m.* FROM message m JOIN interview_session s ON s.id = m.session_id "
            "WHERE m.session_id = ? AND s.user_id = ? ORDER BY m.seq ASC LIMIT ?",
            (session_id, user_id, limit),
        )).fetchall()
        return [dict(row) for row in rows]

    # ---------- recording ----------

    async def recording_create(self, user_id: str, recording: dict) -> dict:
        row = dict(recording)
        row.setdefault("id", str(uuid.uuid4()))
        for key in (
            "filename", "ext", "tos_key", "size_bytes", "asr_task_id", "transcript_json",
            "error", "report_id",
        ):
            row.setdefault(key, None)
        row.setdefault("status", "processing")
        row.setdefault("created_at", utc_now())
        row.setdefault("finished_at", None)
        row["user_id"] = user_id
        await self._c().execute(
            "INSERT INTO recording (id, user_id, filename, ext, tos_key, size_bytes, status, "
            "asr_task_id, transcript_json, error, report_id, created_at, finished_at) VALUES "
            "(:id, :user_id, :filename, :ext, :tos_key, :size_bytes, :status, :asr_task_id, "
            ":transcript_json, :error, :report_id, :created_at, :finished_at)", row,
        )
        await self._c().commit()
        return row

    async def recording_get(self, user_id: str, recording_id: str) -> dict | None:
        return await self._owned_get("recording", user_id, recording_id)

    async def recording_get_internal(self, recording_id: str) -> dict | None:
        row = await (await self._c().execute(
            "SELECT * FROM recording WHERE id = ?", (recording_id,),
        )).fetchone()
        return dict(row) if row else None

    async def recording_update(self, user_id: str, recording_id: str, patch: dict) -> dict | None:
        fields = {key: patch[key] for key in patch if key in self.RECORDING_COLS}
        if not fields:
            return await self.recording_get(user_id, recording_id)
        sets = ", ".join(f"{key} = :{key}" for key in fields)
        await self._c().execute(
            f"UPDATE recording SET {sets} WHERE id = :id AND user_id = :user_id",
            {**fields, "id": recording_id, "user_id": user_id},
        )
        await self._c().commit()
        return await self.recording_get(user_id, recording_id)

    async def recording_list_processing(self) -> list[dict]:
        rows = await (await self._c().execute(
            "SELECT * FROM recording WHERE status = 'processing' ORDER BY created_at ASC"
        )).fetchall()
        return [dict(row) for row in rows]

    # ---------- report ----------

    async def report_create(self, user_id: str, report: dict) -> dict:
        row = dict(report)
        if row.get("session_id") and not await self.session_get(user_id, row["session_id"]):
            raise ValueError("session does not belong to user")
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("md_path", None)
        row.setdefault("position", None)
        row.setdefault("source", "session")
        row.setdefault("created_at", utc_now())
        row["user_id"] = user_id
        await self._c().execute(
            "INSERT INTO interview_report (id, user_id, session_id, scores_json, feedback_json, "
            "suggestions_json, md_path, position, source, created_at) VALUES (:id, :user_id, "
            ":session_id, :scores_json, :feedback_json, :suggestions_json, :md_path, :position, "
            ":source, :created_at)", row,
        )
        await self._c().commit()
        return row

    async def report_get(self, user_id: str, report_id: str) -> dict | None:
        return await self._owned_get("interview_report", user_id, report_id)

    async def report_list(self, user_id: str) -> list[dict]:
        rows = await (await self._c().execute(
            "SELECT * FROM interview_report WHERE user_id = ? ORDER BY created_at DESC", (user_id,),
        )).fetchall()
        return [dict(row) for row in rows]
