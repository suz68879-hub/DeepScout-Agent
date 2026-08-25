"""Phase 2 双副本无状态一致性验收。"""
import os
from pathlib import Path

import httpx
import pytest

from scripts.multi_replica_acceptance import (
    RoundRobinClient,
    build_child_environment,
    run_acceptance,
    validate_test_redis_url,
)


async def test_round_robin_client_alternates_replicas():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    client = RoundRobinClient(
        ["http://127.0.0.1:3101", "http://127.0.0.1:3102"],
        transport=httpx.MockTransport(handler),
    )
    try:
        for _ in range(4):
            response = await client.request("GET", "/health/live")
            assert response.status_code == 200
    finally:
        await client.aclose()

    assert seen == [
        "http://127.0.0.1:3101/health/live",
        "http://127.0.0.1:3102/health/live",
        "http://127.0.0.1:3101/health/live",
        "http://127.0.0.1:3102/health/live",
    ]


def test_child_environment_is_isolated_and_disables_external_recovery():
    parent = {
        "DATABASE_URL": "configured",
        "MIGRATION_DATABASE_URL": "privileged",
        "POSTGRES_ADMIN_URL": "admin",
        "KEEP": "yes",
    }

    child = build_child_environment(
        parent,
        redis_url="redis://127.0.0.1:6379/15",
        rtc_counter_key="deepscout:test:acceptance:rtc:abc",
    )

    assert parent == {
        "DATABASE_URL": "configured",
        "MIGRATION_DATABASE_URL": "privileged",
        "POSTGRES_ADMIN_URL": "admin",
        "KEEP": "yes",
    }
    assert child["APP_ENV"] == "test"
    assert child["STORAGE_BACKEND"] == "postgres"
    assert child["AUTH_SESSION_CACHE_ENABLED"] == "true"
    assert child["REDIS_URL"] == "redis://127.0.0.1:6379/15"
    assert child["TOS_ACCESS_KEY"] == ""
    assert child["TOS_SECRET_KEY"] == ""
    assert child["TOS_BUCKET"] == ""
    assert child["VOLC_ACCESS_KEY"] == ""
    assert child["ARK_API_KEY"] == ""
    assert child["RTC_APP_KEY"] == ""
    assert "MIGRATION_DATABASE_URL" not in child
    assert "POSTGRES_ADMIN_URL" not in child


@pytest.mark.parametrize(
    "url",
    [
        "redis://127.0.0.1:6379/0",
        "redis://127.0.0.1:6379/",
        "https://127.0.0.1:6379/15",
    ],
)
def test_acceptance_rejects_non_dedicated_redis_database(url):
    with pytest.raises(ValueError, match="dedicated Redis database"):
        validate_test_redis_url(url)


def test_powershell_wrapper_enables_isolated_multi_replica_run():
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "verify_stateless_api.ps1"
    ).read_text(encoding="utf-8")

    assert "redis://127.0.0.1:6379/15" in script
    assert "RUN_MULTI_REPLICA_TEST" in script
    assert "MULTI_REPLICA_ROUNDS" in script
    assert "--basetemp" in script


@pytest.mark.skipif(
    os.getenv("RUN_MULTI_REPLICA_TEST") != "1",
    reason="set RUN_MULTI_REPLICA_TEST=1 via verify_stateless_api.ps1",
)
async def test_two_processes_share_state_and_survive_replica_termination():
    rounds = int(os.getenv("MULTI_REPLICA_ROUNDS", "100"))
    redis_url = os.getenv("MULTI_REPLICA_REDIS_URL", "redis://127.0.0.1:6379/15")

    report = await run_acceptance(rounds=rounds, redis_url=redis_url)

    assert report["rounds"] == rounds
    assert report["idempotent_business_effects"] == 1
    assert report["rtc_provider_calls"] == 1
    assert report["rate_limit_bypasses"] == 0
    assert report["failover_recovered"] is True
