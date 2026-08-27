from prometheus_client import CollectorRegistry

from observability.metrics import ServiceMetrics

_ALLOWED_LABELS = {
    "collector",
    "error_type",
    "job_type",
    "le",
    "method",
    "operation",
    "outcome",
    "provider",
    "queue",
    "route",
    "status_class",
}


def test_metric_labels_and_series_count_remain_bounded():
    metrics = ServiceMetrics(CollectorRegistry())
    identifier = "550e8400-e29b-41d4-a716-446655440000"
    filename = "candidate-private-resume.pdf"

    metrics.record_http("BREW", "", 200, 0.1)
    metrics.record_external(identifier, filename, "unexpected", 0.2)
    metrics.record_job(identifier, "unexpected", 2)
    metrics.set_queue_depth(identifier, 3)
    metrics.set_queue_oldest_age(identifier, 4)
    metrics.record_redis_error(identifier, "unexpected")
    metrics.record_collection_error(identifier)

    series = []
    for family in metrics.registry.collect():
        for sample in family.samples:
            assert set(sample.labels) <= _ALLOWED_LABELS
            encoded = str((sample.name, sample.labels))
            assert identifier not in encoded
            assert filename not in encoded
            series.append((sample.name, tuple(sorted(sample.labels.items()))))

    assert len(set(series)) <= 120
