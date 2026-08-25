"""Phase 2 双副本无状态 API 验收编排。"""
from __future__ import annotations

import asyncio
import argparse
import hashlib
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv


class RoundRobinClient:
    """在两个 API 副本之间严格轮询，并共享同一个 Cookie jar。"""

    def __init__(
        self,
        base_urls: Sequence[str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if len(base_urls) != 2:
            raise ValueError("exactly two replica URLs are required")
        self._base_urls = tuple(url.rstrip("/") for url in base_urls)
        self._next_index = 0
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=30.0,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=40),
        )

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        index = self._next_index
        self._next_index = (self._next_index + 1) % len(self._base_urls)
        return await self.request_to(index, method, path, **kwargs)

    async def request_to(
        self, index: int, method: str, path: str, **kwargs
    ) -> httpx.Response:
        return await self._client.request(
            method,
            f"{self._base_urls[index]}{path}",
            **kwargs,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def validate_test_redis_url(url: str) -> None:
    parsed = urlsplit(url)
    database = parsed.path.lstrip("/")
    if (
        parsed.scheme not in {"redis", "rediss"}
        or not parsed.hostname
        or not database.isdigit()
        or int(database) == 0
    ):
        raise ValueError("a dedicated Redis database is required for acceptance")


def build_child_environment(
    parent: Mapping[str, str],
    *,
    redis_url: str,
    rtc_counter_key: str,
) -> dict[str, str]:
    if not parent.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required for multi-replica acceptance")
    validate_test_redis_url(redis_url)
    child = dict(parent)
    child.pop("POSTGRES_ADMIN_URL", None)
    child.pop("MIGRATION_DATABASE_URL", None)
    child.pop("ANALYTICS_DATABASE_URL", None)
    child.update(
        {
            "APP_ENV": "test",
            "STORAGE_BACKEND": "postgres",
            "AUTH_SESSION_CACHE_ENABLED": "true",
            "AUTH_COOKIE_SECURE": "false",
            "REDIS_URL": redis_url,
            "RTC_CALLBACK_SECRET": "p2-t09-test-callback-secret",
            "MULTI_REPLICA_RTC_COUNTER_KEY": rtc_counter_key,
            "TOS_ACCESS_KEY": "",
            "TOS_SECRET_KEY": "",
            "TOS_BUCKET": "",
            "TOS_ENDPOINT": "",
            "TOS_REGION": "",
            "VOLC_ACCESS_KEY": "",
            "VOLC_SECRET_KEY": "",
            "ARK_API_KEY": "",
            "EMBEDDING_API_KEY": "",
            "ASR_FILE_API_KEY": "",
            "RTC_APP_ID": "",
            "RTC_APP_KEY": "",
            "ASR_APP_ID": "",
            "TTS_APP_ID": "",
            "ARK_ENDPOINT_ID": "",
            "SERVER_URL": "",
            "VOLC_ACCOUNT_ID": "",
            "EMBEDDING_API_BASE": "",
        }
    )
    return child


def create_acceptance_app():
    """创建仅供验收进程使用的应用；供应商调用替换为 Redis 计数桩。"""
    from services import rtc_service
    from services.redis_client import redis_error_boundary

    async def fake_provider(action, version, session, incoming_body, lease):
        del version, session, incoming_body
        await lease.assert_owned()
        counter_key = os.environ["MULTI_REPLICA_RTC_COUNTER_KEY"]
        async with redis_error_boundary() as client:
            await client.incr(counter_key)
        await asyncio.sleep(0.05)
        return {
            "ResponseMetadata": {"Action": action},
            "Result": {"AcceptanceStub": True},
        }

    rtc_service._call_provider = fake_provider
    from main import create_app

    return create_app()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_replica(port: int, environment: Mapping[str, str]) -> subprocess.Popen:
    command = [
        sys.executable,
        "-m",
        "scripts.multi_replica_acceptance",
        "serve",
        "--port",
        str(port),
    ]
    return subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


async def _stop_replica(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        await asyncio.to_thread(process.wait, 10)
    except subprocess.TimeoutExpired:
        process.kill()
        await asyncio.to_thread(process.wait, 10)


async def _wait_until_ready(
    base_url: str,
    process: subprocess.Popen,
    *,
    timeout: float = 90.0,
) -> None:
    async def exited_error() -> RuntimeError:
        stderr = await asyncio.to_thread(process.stderr.read)
        safe_error = re.sub(
            r"[a-z][a-z0-9+.-]*://\S+",
            "<redacted-url>",
            stderr,
            flags=re.IGNORECASE,
        )[-2000:]
        return RuntimeError(f"acceptance replica exited during startup: {safe_error}")

    deadline = time.monotonic() + timeout
    last_status: int | None = None
    async with httpx.AsyncClient(timeout=1.0) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise await exited_error()
            try:
                response = await client.get(f"{base_url}/health/ready")
                last_status = response.status_code
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    if process.poll() is not None:
        raise await exited_error()
    process.terminate()
    await asyncio.to_thread(process.wait, 10)
    failure = await exited_error()
    raise RuntimeError(
        f"acceptance replica readiness timed out last_status={last_status}: {failure}"
    )


def _require_status(
    response: httpx.Response,
    expected: int | set[int],
    operation: str,
) -> None:
    accepted = {expected} if isinstance(expected, int) else expected
    if response.status_code not in accepted:
        raise AssertionError(
            f"{operation} returned unexpected status {response.status_code}"
        )


async def _count_user_sessions(database_url: str, user_id: str) -> int:
    import psycopg

    conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    async with await psycopg.AsyncConnection.connect(conninfo) as connection:
        cursor = await connection.execute(
            "SELECT COUNT(*) FROM interview_session WHERE user_id = %s",
            (user_id,),
        )
        row = await cursor.fetchone()
    return int(row[0])


async def _cleanup_postgres(
    database_url: str,
    *,
    username: str,
    session_id: str | None,
) -> None:
    import psycopg
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    async with await psycopg.AsyncConnection.connect(conninfo) as connection:
        cursor = await connection.execute(
            """
            SELECT interview_session.id
            FROM interview_session
            JOIN app_user ON app_user.id = interview_session.user_id
            WHERE app_user.username = %s
            """,
            (username,),
        )
        thread_ids = {str(row[0]) for row in await cursor.fetchall()}
    if session_id:
        thread_ids.add(session_id)
    if thread_ids:
        async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
            for thread_id in thread_ids:
                await saver.adelete_thread(thread_id)
    async with await psycopg.AsyncConnection.connect(conninfo) as connection:
        await connection.execute(
            "DELETE FROM app_user WHERE username = %s",
            (username,),
        )


async def _cleanup_redis(redis_url: str, keys: set[str]) -> None:
    if not keys:
        return
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        await client.delete(*keys)
    finally:
        await client.aclose()


async def _redis_int(redis_url: str, key: str) -> int:
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        return int(await client.get(key) or 0)
    finally:
        await client.aclose()


async def run_acceptance(*, rounds: int, redis_url: str) -> dict[str, Any]:
    if not 1 <= rounds <= 1000:
        raise ValueError("rounds must be between 1 and 1000")
    validate_test_redis_url(redis_url)
    load_dotenv()
    run_id = secrets.token_hex(8)
    counter_digest = hashlib.sha256(run_id.encode()).hexdigest()
    counter_key = f"deepscout:test:acceptance:rtc:{counter_digest}"
    environment = build_child_environment(
        os.environ,
        redis_url=redis_url,
        rtc_counter_key=counter_key,
    )
    first_port = _free_port()
    second_port = _free_port()
    while second_port == first_port:
        second_port = _free_port()
    ports = (first_port, second_port)
    base_urls = tuple(f"http://127.0.0.1:{port}" for port in ports)
    processes = [_start_replica(port, environment) for port in ports]
    startup_timeout = float(os.getenv("MULTI_REPLICA_STARTUP_TIMEOUT", "90"))
    username = f"p2t09_{run_id}"
    password = f"P2t09-{run_id}-pass"
    failed_username = f"missing_{run_id}"
    idempotency_key = f"p2t09-{run_id}-idempotency"
    database_url = environment["DATABASE_URL"]
    user_id: str | None = None
    session_id: str | None = None
    session_tokens: list[str] = []
    redis_keys = {counter_key}
    client: RoundRobinClient | None = None

    try:
        await asyncio.gather(
            *(
                _wait_until_ready(base_url, process, timeout=startup_timeout)
                for base_url, process in zip(base_urls, processes, strict=True)
            )
        )
        client = RoundRobinClient(base_urls)

        registered = await client.request(
            "POST",
            "/api/auth/register",
            json={"username": username, "password": password},
        )
        _require_status(registered, 201, "register")
        user_id = registered.json()["id"]
        registration_token = client.cookies.get("interview_session")
        if not registration_token:
            raise AssertionError("register did not issue an authentication cookie")
        session_tokens.append(registration_token)

        _require_status(await client.request("GET", "/api/auth/me"), 200, "shared login")
        _require_status(await client.request("POST", "/api/auth/logout"), 204, "logout")
        _require_status(
            await client.request("GET", "/api/auth/me"), 401, "shared logout"
        )
        logged_in = await client.request(
            "POST",
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        _require_status(logged_in, 200, "login")
        login_token = client.cookies.get("interview_session")
        if not login_token:
            raise AssertionError("login did not issue an authentication cookie")
        session_tokens.append(login_token)
        _require_status(await client.request("GET", "/api/auth/me"), 200, "login replay")

        rate_responses = await asyncio.gather(
            *(
                client.request(
                    "POST",
                    "/api/auth/login",
                    json={"username": failed_username, "password": "wrong-password"},
                )
                for _ in range(6)
            )
        )
        rate_statuses = [response.status_code for response in rate_responses]
        rate_limit_bypasses = max(0, rate_statuses.count(401) - 5)
        if rate_statuses.count(401) != 5 or rate_statuses.count(429) != 1:
            raise AssertionError("login rate limit was not shared by both replicas")

        write_responses = await asyncio.gather(
            *(
                client.request(
                    "POST",
                    "/api/interview/start",
                    headers={"Idempotency-Key": idempotency_key},
                    json={"position": "P2-T09 acceptance"},
                )
                for _ in range(rounds)
            )
        )
        for response in write_responses:
            _require_status(response, {200, 409}, "idempotent write")
        session_ids = {
            response.json()["session_id"]
            for response in write_responses
            if response.status_code == 200
        }
        if len(session_ids) != 1:
            raise AssertionError("idempotent requests produced multiple sessions")
        session_id = session_ids.pop()
        idempotent_business_effects = await _count_user_sessions(database_url, user_id)
        if idempotent_business_effects != 1:
            raise AssertionError("idempotent requests produced duplicate business rows")

        rtc_path = "/proxy?Action=StartVoiceChat&Version=2024-12-01"
        rtc_responses = await asyncio.gather(
            client.request(
                "POST",
                rtc_path,
                json={"SessionId": session_id},
            ),
            client.request(
                "POST",
                rtc_path,
                json={"SessionId": session_id},
            ),
        )
        for response in rtc_responses:
            _require_status(response, {200, 409}, "RTC start")
        final_rtc = await client.request(
            "POST", rtc_path, json={"SessionId": session_id}
        )
        _require_status(final_rtc, 200, "RTC convergence")
        rtc_provider_calls = await _redis_int(redis_url, counter_key)
        if rtc_provider_calls != 1:
            raise AssertionError("RTC provider was called more than once")

        victim = secrets.randbelow(2)
        survivor = 1 - victim
        await _stop_replica(processes[victim])
        _require_status(
            await client.request_to(survivor, "GET", "/api/auth/me"),
            200,
            "session failover",
        )
        replay = await client.request_to(
            survivor,
            "POST",
            "/api/interview/start",
            headers={"Idempotency-Key": idempotency_key},
            json={"position": "P2-T09 acceptance"},
        )
        _require_status(replay, 200, "idempotency failover")
        if replay.json()["session_id"] != session_id:
            raise AssertionError("idempotency result changed after replica termination")
        if replay.headers.get("Idempotency-Replayed") != "true":
            raise AssertionError("idempotency result was not replayed after failover")
        _require_status(
            await client.request_to(
                survivor,
                "POST",
                rtc_path,
                json={"SessionId": session_id},
            ),
            200,
            "RTC failover",
        )
        if await _redis_int(redis_url, counter_key) != 1:
            raise AssertionError("RTC provider repeated after replica termination")

        return {
            "rounds": rounds,
            "idempotent_business_effects": idempotent_business_effects,
            "rtc_provider_calls": rtc_provider_calls,
            "rate_limit_bypasses": rate_limit_bypasses,
            "failover_recovered": True,
        }
    finally:
        if client is not None:
            await client.aclose()
        await asyncio.gather(*(_stop_replica(process) for process in processes))
        from services.redis_keys import (
            auth_session_key,
            idempotency_record_key,
            login_rate_limit_key,
            register_rate_limit_key,
            rtc_fence_key,
            rtc_lock_key,
        )

        redis_keys.update(
            auth_session_key("test", hashlib.sha256(token.encode()).hexdigest())
            for token in session_tokens
        )
        redis_keys.update(
            {
                register_rate_limit_key("test", "127.0.0.1"),
                login_rate_limit_key("test", "127.0.0.1", username),
                login_rate_limit_key("test", "127.0.0.1", failed_username),
            }
        )
        if session_id:
            redis_keys.update(
                {
                    rtc_lock_key("test", session_id),
                    rtc_fence_key("test", session_id),
                }
            )
        if user_id:
            redis_keys.add(
                idempotency_record_key(
                    "test",
                    user_id,
                    "POST",
                    "/api/interview/start",
                    idempotency_key,
                )
            )
        await _cleanup_redis(redis_url, redis_keys)
        await _cleanup_postgres(
            database_url,
            username=username,
            session_id=session_id,
        )


def _serve(port: int) -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    import uvicorn

    uvicorn.run(
        "scripts.multi_replica_acceptance:create_acceptance_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        log_level="error",
        loop="asyncio",
    )


def _main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if args.command == "serve":
        _serve(args.port)


if __name__ == "__main__":
    _main()
