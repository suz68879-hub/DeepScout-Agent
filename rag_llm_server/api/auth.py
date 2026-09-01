"""Username/password authentication backed by opaque HttpOnly sessions."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from config import settings
from services.auth_service import (
    create_session_token,
    hash_password_async,
    invite_code_accepted,
    normalize_username,
    token_digest,
    verify_password_async,
)
from services.rate_limit import RateLimitDecision, RateLimiter
from services.redis_client import SharedStateUnavailable, get_redis
from services.session_cache import SessionCache
from services.storage import storage
from services.storage.base import StorageConflictError

COOKIE_NAME = "interview_session"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60
router = APIRouter(prefix="/api/auth", tags=["auth"])
_rate_limiter: RateLimiter | None = None


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)


class RegisterCredentials(Credentials):
    invite_code: str = Field(default="", max_length=128)


class LoginCredentials(BaseModel):
    username: str
    password: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _public_user(user: dict) -> dict[str, str]:
    return {"id": user["id"], "username": user["username"]}


def get_session_cache() -> SessionCache:
    return SessionCache(get_redis(), settings.APP_ENV)


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    client = get_redis()
    if _rate_limiter is None or not _rate_limiter.matches(client, settings.APP_ENV):
        _rate_limiter = RateLimiter(client, settings.APP_ENV)
    return _rate_limiter


def _shared_state_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="shared state unavailable")


def _rate_limit_error(decision: RateLimitDecision, detail: str) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=detail,
        headers={"Retry-After": str(decision.retry_after)},
    )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


async def _issue_session(response: Response, user: dict) -> dict[str, str]:
    token, digest, expires_at = create_session_token()
    await storage.auth_session_create(user["id"], digest, expires_at)
    if settings.AUTH_SESSION_CACHE_ENABLED:
        try:
            await get_session_cache().write(
                digest,
                {"user": user, "expires_at": expires_at},
            )
        except SharedStateUnavailable:
            await storage.auth_session_revoke(digest)
            raise _shared_state_unavailable() from None
    _set_session_cookie(response, token)
    return _public_user(user)


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    digest = token_digest(token)
    if settings.AUTH_SESSION_CACHE_ENABLED:
        try:
            user = await get_session_cache().resolve(
                digest,
                lambda: storage.auth_session_get(digest),
            )
        except SharedStateUnavailable:
            raise _shared_state_unavailable() from None
    else:
        user = await storage.auth_session_get_user(digest)
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


async def require_user_quota(user: dict = Depends(get_current_user)) -> dict:
    try:
        decision = await get_rate_limiter().consume_expensive(user["id"])
    except SharedStateUnavailable:
        raise _shared_state_unavailable() from None
    if not decision.allowed:
        raise _rate_limit_error(decision, "too many requests")
    return user


@router.post("/register", status_code=201)
async def register(body: RegisterCredentials, request: Request, response: Response):
    try:
        decision = await get_rate_limiter().consume_register(_client_ip(request))
    except SharedStateUnavailable:
        raise _shared_state_unavailable() from None
    if not decision.allowed:
        raise _rate_limit_error(decision, "too many registration attempts")
    if not invite_code_accepted(body.invite_code, settings.REGISTER_INVITE_CODE):
        raise HTTPException(status_code=403, detail="invalid invite code")
    try:
        username = normalize_username(body.username)
        password_hash = await hash_password_async(body.password)
        user = await storage.user_create(username, password_hash)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StorageConflictError as exc:
        raise HTTPException(status_code=409, detail="username already exists") from exc
    return await _issue_session(response, user)


@router.post("/login")
async def login(body: LoginCredentials, request: Request, response: Response):
    raw_username = body.username.lower()
    client_ip = _client_ip(request)
    try:
        decision = await get_rate_limiter().consume_login(client_ip, raw_username)
    except SharedStateUnavailable:
        raise _shared_state_unavailable() from None
    if not decision.allowed:
        raise _rate_limit_error(decision, "too many login attempts")
    try:
        username = normalize_username(body.username)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid username or password") from None
    user = await storage.user_get_by_username(username)
    password_valid = 10 <= len(body.password) <= 128
    if not user or not password_valid or not await verify_password_async(
        body.password, user["password_hash"],
    ):
        raise HTTPException(status_code=401, detail="invalid username or password")
    try:
        await get_rate_limiter().clear_login(client_ip, raw_username)
    except SharedStateUnavailable:
        raise _shared_state_unavailable() from None
    return await _issue_session(response, user)


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return _public_user(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, user: dict = Depends(get_current_user)):
    del user
    token = request.cookies[COOKIE_NAME]
    digest = token_digest(token)
    if settings.AUTH_SESSION_CACHE_ENABLED:
        try:
            await get_session_cache().delete(digest)
        except SharedStateUnavailable:
            raise _shared_state_unavailable() from None
    await storage.auth_session_revoke(digest)
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    return response
