"""Redis-backed idempotency boundary for authenticated critical writes."""
import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from config import settings
from services.redis_client import SharedStateUnavailable, get_redis, redis_error_boundary
from services.redis_keys import (
    IDEMPOTENCY_TTL_SECONDS,
    RedisDataError,
    decode_payload,
    encode_payload,
    idempotency_record_key,
)

DEFAULT_PROTECTED_ROUTES = {
    "/api/interview/start",
    "/api/interview/finish",
    "/api/recording/upload",
}
PROCESSING_TTL_SECONDS = 5 * 60
_RECORD_FIELDS = {
    "state",
    "body_hash",
    "owner_token",
    "status_code",
    "response_body",
    "content_type",
}
_COMPLETE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
local record = cjson.decode(current)
if record['owner_token'] ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
_RELEASE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
local record = cjson.decode(current)
if record['owner_token'] ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


def validate_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not 16 <= len(value) <= 128:
        raise ValueError("invalid Idempotency-Key")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in value):
        raise ValueError("invalid Idempotency-Key")
    return value


def digest_body(value: Any) -> str:
    try:
        canonical = json.dumps(
            jsonable_encoder(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("request body is not JSON serializable") from exc
    return hashlib.sha256(canonical).hexdigest()


class IdempotencyKeyMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        protected_routes: Collection[str] = DEFAULT_PROTECTED_ROUTES,
    ) -> None:
        super().__init__(app)
        self._protected_routes = frozenset(protected_routes)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method == "POST" and request.url.path in self._protected_routes:
            raw_key = request.headers.get("Idempotency-Key")
            if raw_key is not None:
                try:
                    request.state.idempotency_key = validate_idempotency_key(raw_key)
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "invalid Idempotency-Key"},
                    )
        return await call_next(request)


@dataclass(frozen=True)
class IdempotencyDecision:
    state: str
    owner_token: str | None = None
    status_code: int | None = None
    response_body: Any = None
    content_type: str = "application/json"


class IdempotencyStore:
    def __init__(
        self,
        client: Redis,
        app_env: str,
        *,
        completed_ttl: int = IDEMPOTENCY_TTL_SECONDS,
        processing_ttl: int = PROCESSING_TTL_SECONDS,
        wait_timeout: float = 0.5,
        poll_interval: float = 0.02,
    ) -> None:
        if completed_ttl <= 0 or processing_ttl <= 0:
            raise ValueError("idempotency TTL must be positive")
        if wait_timeout < 0 or poll_interval <= 0:
            raise ValueError("idempotency wait settings are invalid")
        self._client = client
        self._app_env = app_env
        self._completed_ttl = completed_ttl
        self._processing_ttl = processing_ttl
        self._wait_timeout = wait_timeout
        self._poll_interval = poll_interval

    def matches(self, client: Redis, app_env: str) -> bool:
        return self._client is client and self._app_env == app_env

    def record_key(
        self, user_id: str, method: str, route: str, idempotency_key: str
    ) -> str:
        return idempotency_record_key(
            self._app_env, user_id, method, route, idempotency_key
        )

    @staticmethod
    def _decode(raw: str, body_hash: str) -> IdempotencyDecision:
        payload = decode_payload(raw, allowed_fields=_RECORD_FIELDS)
        if payload.get("body_hash") != body_hash:
            return IdempotencyDecision("conflict")
        state = payload.get("state")
        if state == "processing":
            if not isinstance(payload.get("owner_token"), str):
                raise RedisDataError("idempotency owner is invalid")
            return IdempotencyDecision("processing")
        if state == "completed":
            status_code = payload.get("status_code")
            content_type = payload.get("content_type")
            if (
                type(status_code) is not int
                or not 200 <= status_code < 400
                or content_type != "application/json"
                or "response_body" not in payload
            ):
                raise RedisDataError("completed idempotency record is invalid")
            return IdempotencyDecision(
                "completed",
                status_code=status_code,
                response_body=payload["response_body"],
                content_type=content_type,
            )
        raise RedisDataError("idempotency state is invalid")

    async def begin(
        self,
        user_id: str,
        method: str,
        route: str,
        idempotency_key: str,
        body_hash: str,
    ) -> IdempotencyDecision:
        key = self.record_key(user_id, method, route, idempotency_key)
        owner_token = secrets.token_urlsafe(32)
        processing = encode_payload(
            {
                "state": "processing",
                "body_hash": body_hash,
                "owner_token": owner_token,
            },
            allowed_fields=_RECORD_FIELDS,
        )
        deadline = time.monotonic() + self._wait_timeout
        try:
            async with redis_error_boundary(self._client) as client:
                while True:
                    acquired = await client.set(
                        key, processing, nx=True, ex=self._processing_ttl
                    )
                    if acquired:
                        return IdempotencyDecision("acquired", owner_token=owner_token)
                    raw = await client.get(key)
                    if raw is None:
                        continue
                    decision = self._decode(raw, body_hash)
                    if decision.state != "processing":
                        return decision
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return decision
                    await asyncio.sleep(min(self._poll_interval, remaining))
        except RedisDataError as exc:
            raise SharedStateUnavailable("Redis shared state is unavailable") from exc

    async def complete(
        self,
        key: str,
        owner_token: str,
        body_hash: str,
        status_code: int,
        response_body: Any,
    ) -> None:
        completed = encode_payload(
            {
                "state": "completed",
                "body_hash": body_hash,
                "status_code": status_code,
                "response_body": jsonable_encoder(response_body),
                "content_type": "application/json",
            },
            allowed_fields=_RECORD_FIELDS,
        )
        async with redis_error_boundary(self._client) as client:
            updated = await client.eval(
                _COMPLETE_SCRIPT,
                1,
                key,
                owner_token,
                completed,
                self._completed_ttl,
            )
        if not updated:
            raise SharedStateUnavailable("Redis shared state is unavailable")

    async def release(self, key: str, owner_token: str) -> None:
        async with redis_error_boundary(self._client) as client:
            await client.eval(_RELEASE_SCRIPT, 1, key, owner_token)


_store: IdempotencyStore | None = None


def get_idempotency_store() -> IdempotencyStore:
    global _store
    client = get_redis()
    if _store is None or not _store.matches(client, settings.APP_ENV):
        _store = IdempotencyStore(client, settings.APP_ENV)
    return _store


def _request_key(request: Request) -> str | None:
    state_key = getattr(request.state, "idempotency_key", None)
    if state_key is not None:
        return state_key
    raw_key = request.headers.get("Idempotency-Key")
    return validate_idempotency_key(raw_key) if raw_key is not None else None


async def execute_idempotent(
    request: Request | None,
    user: dict,
    body: Any,
    operation: Callable[[], Awaitable[Any]],
    *,
    store: IdempotencyStore | None = None,
    status_code: int = 200,
) -> Any:
    if request is None:
        return await operation()
    try:
        idempotency_key = _request_key(request)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid Idempotency-Key") from None
    if idempotency_key is None:
        return await operation()

    active_store = store or get_idempotency_store()
    body_hash = digest_body(body)
    try:
        decision = await active_store.begin(
            user["id"], request.method, request.url.path, idempotency_key, body_hash
        )
    except SharedStateUnavailable:
        raise HTTPException(status_code=503, detail="shared state unavailable") from None
    if decision.state == "conflict":
        raise HTTPException(
            status_code=409,
            detail="idempotency key conflicts with request body",
        )
    if decision.state == "processing":
        raise HTTPException(
            status_code=409,
            detail="idempotent request is processing",
            headers={"Retry-After": "1"},
        )
    if decision.state == "completed":
        return JSONResponse(
            status_code=decision.status_code,
            content=decision.response_body,
            headers={"Idempotency-Replayed": "true"},
            media_type=decision.content_type,
        )

    record_key = active_store.record_key(
        user["id"], request.method, request.url.path, idempotency_key
    )
    try:
        response_body = await operation()
    except Exception:
        try:
            await active_store.release(record_key, decision.owner_token)
        except SharedStateUnavailable:
            raise HTTPException(
                status_code=503, detail="shared state unavailable"
            ) from None
        raise
    try:
        await active_store.complete(
            record_key,
            decision.owner_token,
            body_hash,
            status_code,
            response_body,
        )
    except (RedisDataError, SharedStateUnavailable):
        raise HTTPException(status_code=503, detail="shared state unavailable") from None
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(response_body),
        headers={"Idempotency-Replayed": "false"},
    )
