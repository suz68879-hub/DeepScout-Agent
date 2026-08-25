import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AppUser, AuthSession
from services.storage.base import StorageConflictError


def _as_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


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
