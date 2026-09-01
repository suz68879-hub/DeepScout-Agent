"""Password and opaque-session primitives for the local multi-user service."""
import asyncio
import hmac
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from pwdlib import PasswordHash

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128
SESSION_DAYS = 7

_password_hash = PasswordHash.recommended()


def normalize_username(username: str) -> str:
    normalized = username.lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError("username must be 3-32 lowercase letters, numbers, '.', '_' or '-'")
    return normalized


def validate_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError("password must be 10-128 characters")
    return password


def hash_password(password: str) -> str:
    return _password_hash.hash(validate_password(password))


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except (TypeError, ValueError):
        return False


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session_token() -> tuple[str, str, str]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    return token, token_digest(token), expires_at.isoformat()


def invite_code_accepted(provided: str | None, configured: str | None) -> bool:
    """未配置邀请码时关闭注册；匹配时用常量时间比较。"""
    expected = (configured or "").strip().encode("utf-8")
    actual = provided.strip().encode("utf-8") if isinstance(provided, str) else b""
    if not expected or not actual or len(actual) != len(expected):
        return False
    return hmac.compare_digest(actual, expected)
