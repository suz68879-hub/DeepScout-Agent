"""Username/password authentication backed by opaque HttpOnly sessions."""
import sqlite3
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from config import settings
from services.auth_service import (
    create_session_token,
    hash_password_async,
    normalize_username,
    token_digest,
    verify_password_async,
)
from services.storage import storage

COOKIE_NAME = "interview_session"
COOKIE_MAX_AGE = 7 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
REGISTER_WINDOW_SECONDS = 60 * 60
REGISTER_MAX_ATTEMPTS = 10

router = APIRouter(prefix="/api/auth", tags=["auth"])
_login_failures: dict[str, deque[float]] = defaultdict(deque)
_register_attempts: dict[str, deque[float]] = defaultdict(deque)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)


class LoginCredentials(BaseModel):
    username: str
    password: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_limited(bucket: deque[float], window: int, maximum: int) -> bool:
    now = time.monotonic()
    while bucket and now - bucket[0] >= window:
        bucket.popleft()
    return len(bucket) >= maximum


def reset_rate_limits() -> None:
    _login_failures.clear()
    _register_attempts.clear()


def _public_user(user: dict) -> dict[str, str]:
    return {"id": user["id"], "username": user["username"]}


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
    _set_session_cookie(response, token)
    return _public_user(user)


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    user = await storage.auth_session_get_user(token_digest(token))
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


@router.post("/register", status_code=201)
async def register(body: Credentials, request: Request, response: Response):
    bucket = _register_attempts[_client_ip(request)]
    if _is_limited(bucket, REGISTER_WINDOW_SECONDS, REGISTER_MAX_ATTEMPTS):
        raise HTTPException(status_code=429, detail="too many registration attempts")
    bucket.append(time.monotonic())
    try:
        username = normalize_username(body.username)
        password_hash = await hash_password_async(body.password)
        user = await storage.user_create(username, password_hash)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="username already exists") from exc
    return await _issue_session(response, user)


@router.post("/login")
async def login(body: LoginCredentials, request: Request, response: Response):
    raw_username = body.username.lower()
    key = f"{_client_ip(request)}:{raw_username[:128]}"
    failures = _login_failures[key]
    if _is_limited(failures, LOGIN_WINDOW_SECONDS, LOGIN_MAX_FAILURES):
        raise HTTPException(status_code=429, detail="too many login attempts")
    try:
        username = normalize_username(body.username)
    except ValueError:
        failures.append(time.monotonic())
        raise HTTPException(status_code=401, detail="invalid username or password") from None
    user = await storage.user_get_by_username(username)
    password_valid = 10 <= len(body.password) <= 128
    if not user or not password_valid or not await verify_password_async(
        body.password, user["password_hash"],
    ):
        failures.append(time.monotonic())
        raise HTTPException(status_code=401, detail="invalid username or password")
    failures.clear()
    return await _issue_session(response, user)


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return _public_user(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, user: dict = Depends(get_current_user)):
    del user
    token = request.cookies[COOKIE_NAME]
    await storage.auth_session_revoke(token_digest(token))
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    return response
