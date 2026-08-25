import uuid
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AppUser, AuthSession, InterviewSession, Message, Resume
from services.storage.base import StorageConflictError, StorageVersionConflictError


def _as_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _optional_datetime(value: str | None) -> datetime | None:
    return _as_datetime(value) if value else None


def _json_load(value):
    return json.loads(value) if isinstance(value, str) else value


def _row_dict(instance, json_fields: set[str] | None = None) -> dict:
    json_fields = json_fields or set()
    row = {}
    for column in instance.__table__.columns:
        value = getattr(instance, column.name)
        if isinstance(value, uuid.UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
        elif column.name in json_fields and value is not None:
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        row[column.name] = value
    return row


def _user_dict(user: AppUser) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "password_hash": user.password_hash,
        "role": user.role,
        "created_at": user.created_at.isoformat(),
    }


class PostgresAuthRepository:
    """绑定单个 AsyncSession；事务提交和回滚由调用方负责。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def user_create(
        self,
        username: str,
        password_hash: str,
        role: str = "user",
    ) -> dict:
        user = AppUser(
            id=uuid.uuid4(),
            username=username,
            password_hash=password_hash,
            role=role,
        )
        self._session.add(user)
        try:
            await self._session.flush()
        except IntegrityError:
            raise StorageConflictError("username already exists") from None
        return _user_dict(user)

    async def user_get_by_username(self, username: str) -> dict | None:
        user = await self._session.scalar(
            select(AppUser).where(func.lower(AppUser.username) == username.lower())
        )
        return _user_dict(user) if user else None

    async def auth_session_create(
        self,
        user_id: str,
        token_hash: str,
        expires_at: str,
    ) -> None:
        self._session.add(
            AuthSession(
                token_hash=token_hash,
                user_id=uuid.UUID(user_id),
                expires_at=_as_datetime(expires_at),
            )
        )
        await self._session.flush()

    async def auth_session_get_user(self, token_hash: str) -> dict | None:
        user = await self._session.scalar(
            select(AppUser)
            .join(AuthSession, AuthSession.user_id == AppUser.id)
            .where(
                AuthSession.token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > func.now(),
            )
        )
        return _user_dict(user) if user else None

    async def auth_session_revoke(self, token_hash: str) -> None:
        await self._session.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )


class PostgresRepository(PostgresAuthRepository):
    RESUME_COLS = {"content", "structured_json", "source", "status"}
    SESSION_COLS = {"resume_id", "position", "stage", "status", "ended_at", "rtc_status"}

    async def resume_create(self, user_id: str, resume: dict) -> dict:
        now = datetime.now(timezone.utc)
        model = Resume(
            id=uuid.UUID(resume["id"]) if resume.get("id") else uuid.uuid4(),
            user_id=uuid.UUID(user_id),
            content=resume.get("content"),
            structured_json=_json_load(resume.get("structured_json")),
            source=resume.get("source"),
            status=resume.get("status"),
            created_at=_optional_datetime(resume.get("created_at")) or now,
            updated_at=_optional_datetime(resume.get("updated_at")) or now,
        )
        self._session.add(model)
        await self._session.flush()
        return _row_dict(model, {"structured_json"})

    async def resume_get(self, user_id: str, resume_id: str) -> dict | None:
        model = await self._session.scalar(
            select(Resume).where(
                Resume.id == uuid.UUID(resume_id), Resume.user_id == uuid.UUID(user_id)
            )
        )
        return _row_dict(model, {"structured_json"}) if model else None

    async def resume_update(
        self, user_id: str, resume_id: str, patch: dict
    ) -> dict | None:
        values = {key: patch[key] for key in patch if key in self.RESUME_COLS}
        if "structured_json" in values:
            values["structured_json"] = _json_load(values["structured_json"])
        if not values:
            return await self.resume_get(user_id, resume_id)
        values["updated_at"] = datetime.now(timezone.utc)
        await self._session.execute(
            update(Resume)
            .where(Resume.id == uuid.UUID(resume_id), Resume.user_id == uuid.UUID(user_id))
            .values(**values)
        )
        return await self.resume_get(user_id, resume_id)

    async def resume_list(self, user_id: str) -> list[dict]:
        models = (
            await self._session.scalars(
                select(Resume)
                .where(Resume.user_id == uuid.UUID(user_id))
                .order_by(Resume.created_at.asc())
            )
        ).all()
        return [_row_dict(model, {"structured_json"}) for model in models]

    async def resume_latest(self, user_id: str) -> dict | None:
        model = await self._session.scalar(
            select(Resume)
            .where(Resume.user_id == uuid.UUID(user_id))
            .order_by(Resume.created_at.desc(), Resume.id.desc())
            .limit(1)
        )
        return _row_dict(model, {"structured_json"}) if model else None

    @staticmethod
    def _new_rtc_ids() -> tuple[str, str, str, str]:
        return (
            f"room_{uuid.uuid4().hex}",
            f"user_{uuid.uuid4().hex}",
            f"task_{uuid.uuid4().hex}",
            secrets.token_urlsafe(24),
        )

    async def session_create(self, user_id: str, session: dict) -> dict:
        if session.get("resume_id") and not await self.resume_get(user_id, session["resume_id"]):
            raise ValueError("resume does not belong to user")
        room_id, rtc_user_id, task_id, callback_id = self._new_rtc_ids()
        model = InterviewSession(
            id=uuid.UUID(session["id"]) if session.get("id") else uuid.uuid4(),
            user_id=uuid.UUID(user_id),
            resume_id=uuid.UUID(session["resume_id"]) if session.get("resume_id") else None,
            position=session.get("position"),
            stage=session.get("stage"),
            status=session.get("status"),
            started_at=_optional_datetime(session.get("started_at"))
            or datetime.now(timezone.utc),
            ended_at=_optional_datetime(session.get("ended_at")),
            rtc_room_id=session.get("rtc_room_id", room_id),
            rtc_user_id=session.get("rtc_user_id", rtc_user_id),
            rtc_task_id=session.get("rtc_task_id", task_id),
            rtc_callback_id=session.get("rtc_callback_id", callback_id),
            rtc_status=session.get("rtc_status", "created"),
            version=session.get("version", 1),
        )
        self._session.add(model)
        try:
            await self._session.flush()
        except IntegrityError:
            raise StorageConflictError("interview session constraint conflict") from None
        return _row_dict(model)

    async def session_get(self, user_id: str, session_id: str) -> dict | None:
        model = await self._session.scalar(
            select(InterviewSession).where(
                InterviewSession.id == uuid.UUID(session_id),
                InterviewSession.user_id == uuid.UUID(user_id),
            )
        )
        return _row_dict(model) if model else None

    async def session_get_internal(self, session_id: str) -> dict | None:
        model = await self._session.get(InterviewSession, uuid.UUID(session_id))
        return _row_dict(model) if model else None

    async def session_get_by_callback(self, callback_id: str) -> dict | None:
        model = await self._session.scalar(
            select(InterviewSession).where(InterviewSession.rtc_callback_id == callback_id)
        )
        return _row_dict(model) if model else None

    async def session_update(
        self,
        user_id: str,
        session_id: str,
        patch: dict,
        expected_version: int | None = None,
    ) -> dict | None:
        values = {key: patch[key] for key in patch if key in self.SESSION_COLS}
        if "resume_id" in values and values["resume_id"] is not None:
            if not await self.resume_get(user_id, values["resume_id"]):
                raise ValueError("resume does not belong to user")
            values["resume_id"] = uuid.UUID(values["resume_id"])
        if "ended_at" in values:
            values["ended_at"] = _optional_datetime(values["ended_at"])
        if not values:
            return await self.session_get(user_id, session_id)
        statement = update(InterviewSession).where(
            InterviewSession.id == uuid.UUID(session_id),
            InterviewSession.user_id == uuid.UUID(user_id),
        )
        if expected_version is not None:
            statement = statement.where(InterviewSession.version == expected_version)
            values["version"] = InterviewSession.version + 1
        model = await self._session.scalar(statement.values(**values).returning(InterviewSession))
        if model is None and expected_version is not None:
            if await self.session_get(user_id, session_id):
                raise StorageVersionConflictError("session version conflict")
        return _row_dict(model) if model else None

    async def session_list_running(self, user_id: str) -> list[dict]:
        models = (
            await self._session.scalars(
                select(InterviewSession)
                .where(
                    InterviewSession.user_id == uuid.UUID(user_id),
                    InterviewSession.status == "running",
                )
                .order_by(InterviewSession.started_at.desc(), InterviewSession.id.desc())
            )
        ).all()
        return [_row_dict(model) for model in models]

    async def message_append(
        self, user_id: str, session_id: str, role: str, content: str
    ) -> dict:
        owned_session = await self._session.scalar(
            select(InterviewSession)
            .where(
                InterviewSession.id == uuid.UUID(session_id),
                InterviewSession.user_id == uuid.UUID(user_id),
            )
            .with_for_update()
        )
        if owned_session is None:
            raise ValueError("session does not belong to user")
        seq = await self._session.scalar(
            select(func.coalesce(func.max(Message.seq), 0) + 1).where(
                Message.session_id == owned_session.id
            )
        )
        model = Message(
            session_id=owned_session.id,
            role=role,
            content=content,
            seq=seq,
        )
        self._session.add(model)
        await self._session.flush()
        return _row_dict(model)

    async def message_list(
        self, user_id: str, session_id: str, limit: int = 100
    ) -> list[dict]:
        models = (
            await self._session.scalars(
                select(Message)
                .join(InterviewSession, InterviewSession.id == Message.session_id)
                .where(
                    Message.session_id == uuid.UUID(session_id),
                    InterviewSession.user_id == uuid.UUID(user_id),
                )
                .order_by(Message.seq.asc())
                .limit(limit)
            )
        ).all()
        return [_row_dict(model) for model in models]
