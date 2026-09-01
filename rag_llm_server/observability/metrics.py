"""Bounded Prometheus metrics for API, jobs and core dependencies."""

import ipaddress
import threading
import time

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

_HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
_PROVIDERS = frozenset({"ark", "asr", "rtc"})
_EXTERNAL_OPERATIONS = frozenset(
    {"chat", "chat_stream", "query", "startvoicechat", "stopvoicechat", "submit"}
)
_OUTCOMES = frozenset(
    {
        "cancelled",
        "connection",
        "failed",
        "provider",
        "rate_limited",
        "rejected",
        "success",
        "succeeded",
        "timeout",
        "upstream",
    }
)
_JOB_TYPES = frozenset({"interview.finish", "recording.process"})
_QUEUES = frozenset({"cold", "outbox", "recording"})
_REDIS_OPERATIONS = frozenset(
    {"DELETE", "EVAL", "EXPIRE", "GET", "INCR", "PING", "PTTL", "SET"}
)
_ERROR_TYPES = frozenset({"cancelled", "connection", "internal", "timeout"})
_COLLECTORS = frozenset({"persistence_backlog"})


def _allowed(value: str, choices: frozenset[str]) -> str:
    return value if value in choices else "other"


def is_internal_metrics_client(host: str | None) -> bool:
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    return address.is_loopback


class FirstTokenTimer:
    def __init__(self, histogram: Histogram) -> None:
        self._histogram = histogram
        self._started_at = time.perf_counter()
        self._observed = False

    def observe(self) -> bool:
        if self._observed:
            return False
        self._observed = True
        self._histogram.observe(max(0.0, time.perf_counter() - self._started_at))
        return True


class ServiceMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self.registry = registry
        self._db_pool_lock = threading.Lock()
        self._db_pool_count = 0
        self.http_requests = Counter(
            "http_server_requests_total",
            "HTTP requests by method, route template and status class.",
            ("method", "route", "status_class"),
            registry=registry,
        )
        self.http_duration = Histogram(
            "http_server_request_duration_seconds",
            "HTTP request duration in seconds.",
            ("method", "route", "status_class"),
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
            registry=registry,
        )
        self.first_token = Histogram(
            "interview_first_token_seconds",
            "Time until the first interview response token.",
            buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 10),
            registry=registry,
        )
        self.external_duration = Histogram(
            "external_request_duration_seconds",
            "External request duration in seconds.",
            ("provider", "operation", "outcome"),
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 120),
            registry=registry,
        )
        self.background_jobs = Counter(
            "background_jobs_total",
            "Terminal background job outcomes.",
            ("job_type", "outcome"),
            registry=registry,
        )
        self.background_job_duration = Histogram(
            "background_job_duration_seconds",
            "Background job duration in seconds.",
            ("job_type", "outcome"),
            buckets=(1, 5, 10, 30, 60, 300, 600, 1200, 3600),
            registry=registry,
        )
        self.queue_depth = Gauge(
            "background_queue_depth",
            "Persisted pending jobs by queue.",
            ("queue",),
            registry=registry,
        )
        self.queue_oldest_age = Gauge(
            "background_queue_oldest_age_seconds",
            "Age of the oldest persisted pending job.",
            ("queue",),
            registry=registry,
        )
        self.outbox_unpublished = Gauge(
            "outbox_unpublished_total",
            "Unpublished outbox events.",
            registry=registry,
        )
        self.outbox_oldest_age = Gauge(
            "outbox_oldest_age_seconds",
            "Age of the oldest unpublished outbox event.",
            registry=registry,
        )
        self.db_pool_in_use = Gauge(
            "db_pool_in_use",
            "Checked out database connections.",
            registry=registry,
        )
        self.db_pool_capacity = Gauge(
            "db_pool_capacity",
            "Configured database pool size including overflow.",
            registry=registry,
        )
        self.redis_errors = Counter(
            "redis_operation_errors_total",
            "Redis command errors by bounded operation and type.",
            ("operation", "error_type"),
            registry=registry,
        )
        self.collection_errors = Counter(
            "observability_collection_errors_total",
            "Errors while refreshing asynchronous metric snapshots.",
            ("collector",),
            registry=registry,
        )

    def record_http(
        self,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        locked_method = _allowed(method.upper(), _HTTP_METHODS)
        locked_route = route if route.startswith("/") and len(route) <= 128 else "unknown"
        status_class = f"{status_code // 100}xx" if 100 <= status_code <= 599 else "other"
        labels = (locked_method, locked_route, status_class)
        self.http_requests.labels(*labels).inc()
        self.http_duration.labels(*labels).observe(max(0.0, duration_seconds))

    def record_first_token(self, duration_seconds: float) -> None:
        self.first_token.observe(max(0.0, duration_seconds))

    def first_token_timer(self) -> FirstTokenTimer:
        return FirstTokenTimer(self.first_token)

    def record_external(
        self,
        provider: str,
        operation: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        labels = (
            _allowed(provider, _PROVIDERS),
            _allowed(operation.lower(), _EXTERNAL_OPERATIONS),
            _allowed(outcome, _OUTCOMES),
        )
        self.external_duration.labels(*labels).observe(max(0.0, duration_seconds))

    def record_job(
        self,
        job_type: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        labels = (
            _allowed(job_type, _JOB_TYPES),
            _allowed(outcome, _OUTCOMES),
        )
        self.background_jobs.labels(*labels).inc()
        self.background_job_duration.labels(*labels).observe(
            max(0.0, duration_seconds)
        )

    def set_queue_depth(self, queue: str, value: int) -> None:
        self.queue_depth.labels(_allowed(queue, _QUEUES)).set(max(0, value))

    def set_queue_oldest_age(self, queue: str, value: float) -> None:
        self.queue_oldest_age.labels(_allowed(queue, _QUEUES)).set(max(0.0, value))

    def set_outbox_unpublished(self, value: int) -> None:
        self.outbox_unpublished.set(max(0, value))

    def set_outbox_oldest_age(self, value: float) -> None:
        self.outbox_oldest_age.set(max(0.0, value))

    def set_db_pool_in_use(self, value: int) -> None:
        with self._db_pool_lock:
            self._db_pool_count = max(0, value)
            self.db_pool_in_use.set(self._db_pool_count)

    def set_db_pool_capacity(self, value: int) -> None:
        self.db_pool_capacity.set(max(0, value))

    def database_connection_checked_out(self) -> None:
        with self._db_pool_lock:
            self._db_pool_count += 1
            self.db_pool_in_use.set(self._db_pool_count)

    def database_connection_checked_in(self) -> None:
        with self._db_pool_lock:
            self._db_pool_count = max(0, self._db_pool_count - 1)
            self.db_pool_in_use.set(self._db_pool_count)

    def record_redis_error(self, operation: str, error_type: str) -> None:
        labels = (
            _allowed(operation.upper(), _REDIS_OPERATIONS),
            _allowed(error_type, _ERROR_TYPES),
        )
        self.redis_errors.labels(*labels).inc()

    def record_collection_error(self, collector: str) -> None:
        self.collection_errors.labels(_allowed(collector, _COLLECTORS)).inc()


service_metrics = ServiceMetrics()
