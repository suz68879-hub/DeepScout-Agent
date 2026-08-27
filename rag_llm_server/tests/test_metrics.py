import httpx
from prometheus_client import CollectorRegistry, generate_latest

from main import create_app
from observability.metrics import ServiceMetrics, is_internal_metrics_client


def _render(metrics: ServiceMetrics) -> str:
    return generate_latest(metrics.registry).decode("utf-8")


def test_core_metrics_have_slo_buckets_and_fixed_labels():
    metrics = ServiceMetrics(CollectorRegistry())

    metrics.record_http("GET", "/api/jobs/{job_id}", 200, 0.42)
    metrics.record_first_token(1.2)
    metrics.record_external("ark", "chat", "success", 0.8)
    metrics.record_job("interview.finish", "succeeded", 12.0)
    metrics.set_queue_depth("cold", 3)
    metrics.set_queue_oldest_age("cold", 45)
    metrics.set_outbox_unpublished(2)
    metrics.set_outbox_oldest_age(8)
    metrics.set_db_pool_in_use(4)
    metrics.set_db_pool_capacity(10)
    metrics.record_redis_error("GET", "timeout")

    output = _render(metrics)
    assert 'http_server_requests_total{method="GET",route="/api/jobs/{job_id}",status_class="2xx"} 1.0' in output
    assert 'http_server_request_duration_seconds_bucket{le="0.5",method="GET",route="/api/jobs/{job_id}",status_class="2xx"} 1.0' in output
    assert 'interview_first_token_seconds_bucket{le="2.0"} 1.0' in output
    assert 'external_request_duration_seconds_count{operation="chat",outcome="success",provider="ark"} 1.0' in output
    assert 'background_jobs_total{job_type="interview.finish",outcome="succeeded"} 1.0' in output
    assert 'background_queue_depth{queue="cold"} 3.0' in output
    assert 'background_queue_oldest_age_seconds{queue="cold"} 45.0' in output
    assert "outbox_unpublished_total 2.0" in output
    assert "outbox_oldest_age_seconds 8.0" in output
    assert "db_pool_in_use 4.0" in output
    assert "db_pool_capacity 10.0" in output
    assert 'redis_operation_errors_total{error_type="timeout",operation="GET"} 1.0' in output


def test_unknown_labels_are_bounded_and_identifiers_are_never_labels():
    metrics = ServiceMetrics(CollectorRegistry())
    secret_id = "550e8400-e29b-41d4-a716-446655440000"

    metrics.record_http("BREW", "", 599, 0.01)
    metrics.record_external("private-provider", secret_id, "odd", 0.01)
    metrics.record_job(secret_id, "odd", 0.01)
    metrics.set_queue_depth(secret_id, 1)
    metrics.record_redis_error(secret_id, "odd")

    output = _render(metrics)
    assert secret_id not in output
    assert 'method="other"' in output
    assert 'route="unknown"' in output
    assert 'provider="other"' in output
    assert 'operation="other"' in output
    assert 'job_type="other"' in output
    assert 'queue="other"' in output


def test_first_token_timer_observes_only_first_chunk(monkeypatch):
    metrics = ServiceMetrics(CollectorRegistry())
    ticks = iter([10.0, 10.4, 11.0])
    monkeypatch.setattr("observability.metrics.time.perf_counter", lambda: next(ticks))
    timer = metrics.first_token_timer()

    assert timer.observe() is True
    assert timer.observe() is False

    output = _render(metrics)
    assert "interview_first_token_seconds_count 1.0" in output
    assert "interview_first_token_seconds_sum 0.400" in output


def test_db_pool_gauge_is_concurrency_safe_and_never_negative():
    metrics = ServiceMetrics(CollectorRegistry())

    metrics.database_connection_checked_in()
    metrics.database_connection_checked_out()
    metrics.database_connection_checked_out()
    metrics.database_connection_checked_in()

    assert "db_pool_in_use 1.0" in _render(metrics)


def test_metrics_endpoint_allows_only_private_or_loopback_clients():
    assert is_internal_metrics_client("127.0.0.1") is True
    assert is_internal_metrics_client("10.1.2.3") is True
    assert is_internal_metrics_client("8.8.8.8") is False
    assert is_internal_metrics_client("not-an-ip") is False


async def test_metrics_endpoint_rejects_public_client_and_serves_loopback():
    application = create_app()
    private_transport = httpx.ASGITransport(
        app=application,
        client=("127.0.0.1", 12345),
    )
    async with httpx.AsyncClient(
        transport=private_transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_server_requests_total" in response.text

    public_transport = httpx.ASGITransport(
        app=application,
        client=("8.8.8.8", 12345),
    )
    async with httpx.AsyncClient(
        transport=public_transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/metrics")
    assert response.status_code == 403


async def test_metric_recording_failure_does_not_change_http_result(monkeypatch):
    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr(
        "middleware.request_context.service_metrics.record_http",
        fail,
    )
    application = create_app()
    transport = httpx.ASGITransport(
        app=application,
        client=("127.0.0.1", 12345),
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}
