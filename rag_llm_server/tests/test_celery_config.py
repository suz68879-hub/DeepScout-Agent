"""Celery/RabbitMQ 配置、路由与生产保护测试。"""

import os
from uuid import uuid4

import pytest

from config import Config


def _clear_celery_env(monkeypatch):
    for key in (
        "CELERY_BROKER_URL",
        "CELERY_BROKER_CA_CERT",
        "CELERY_BROKER_CLIENT_CERT",
        "CELERY_BROKER_CLIENT_KEY",
        "CELERY_BROKER_CONNECTION_TIMEOUT",
        "CELERY_BROKER_MAX_RETRIES",
        "CELERY_COLD_WORKER_CONCURRENCY",
        "CELERY_RECORDING_WORKER_CONCURRENCY",
        "CELERY_OUTBOX_WORKER_CONCURRENCY",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_production_dependencies(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:safe-secret@db.internal/deepscout",
    )
    monkeypatch.setenv("REDIS_URL", "rediss://cache.internal/0")


def test_celery_config_defaults_to_disabled_outside_production(monkeypatch):
    _clear_celery_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")

    config = Config()

    assert config.CELERY_BROKER_URL is None
    assert config.CELERY_BROKER_CONNECTION_TIMEOUT == 5.0
    assert config.CELERY_BROKER_MAX_RETRIES == 3
    assert config.CELERY_COLD_WORKER_CONCURRENCY == 2
    assert config.CELERY_RECORDING_WORKER_CONCURRENCY == 2
    assert config.CELERY_OUTBOX_WORKER_CONCURRENCY == 1
    assert config.celery_broker_log_target() is None


def test_production_requires_tls_rabbitmq_broker(monkeypatch):
    _clear_celery_env(monkeypatch)
    _set_production_dependencies(monkeypatch)

    with pytest.raises(ValueError, match="CELERY_BROKER_URL is required"):
        Config()

    monkeypatch.setenv("CELERY_BROKER_URL", "amqp://app:secret@mq.internal/vhost")
    with pytest.raises(ValueError, match="must use amqps"):
        Config()


@pytest.mark.parametrize(
    "url",
    (
        "memory://",
        "redis://cache.internal/0",
        "http://mq.internal",
        "amqps://",
        "not-a-url",
    ),
)
def test_celery_config_rejects_non_rabbitmq_urls_without_leaking_secret(
    monkeypatch, url
):
    _clear_celery_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CELERY_BROKER_URL", url)

    with pytest.raises(ValueError, match="CELERY_BROKER_URL must be") as exc_info:
        Config()

    assert "secret@" not in str(exc_info.value)


def test_celery_config_redacts_broker_credentials(monkeypatch):
    _clear_celery_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "CELERY_BROKER_URL",
        "amqps://deepscout:super-secret@mq.internal:5671/deepscout",
    )

    config = Config()

    assert config.celery_broker_log_target() == "mq.internal:5671/deepscout"
    assert "super-secret" not in config.celery_broker_log_target()


def test_celery_config_rejects_missing_or_incomplete_certificates(
    monkeypatch, tmp_path
):
    _clear_celery_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CELERY_BROKER_URL", "amqps://mq.internal/deepscout")
    monkeypatch.setenv("CELERY_BROKER_CA_CERT", str(tmp_path / "missing-ca.pem"))

    with pytest.raises(ValueError, match="CELERY_BROKER_CA_CERT does not exist"):
        Config()

    monkeypatch.delenv("CELERY_BROKER_CA_CERT")
    client_cert = tmp_path / "client.pem"
    client_cert.write_text("test certificate", encoding="utf-8")
    monkeypatch.setenv("CELERY_BROKER_CLIENT_CERT", str(client_cert))

    with pytest.raises(ValueError, match="must be configured together"):
        Config()


def test_celery_config_rejects_invalid_certificate_content_without_path_leak(
    monkeypatch, tmp_path
):
    _clear_celery_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CELERY_BROKER_URL", "amqps://mq.internal/deepscout")
    invalid_ca = tmp_path / "private-location" / "invalid-ca.pem"
    invalid_ca.parent.mkdir()
    invalid_ca.write_text("not a certificate", encoding="utf-8")
    monkeypatch.setenv("CELERY_BROKER_CA_CERT", str(invalid_ca))

    with pytest.raises(ValueError, match="certificate configuration is invalid") as exc:
        Config()

    assert str(invalid_ca) not in str(exc.value)


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("CELERY_BROKER_CONNECTION_TIMEOUT", "0"),
        ("CELERY_BROKER_CONNECTION_TIMEOUT", "nan"),
        ("CELERY_BROKER_MAX_RETRIES", "-1"),
        ("CELERY_COLD_WORKER_CONCURRENCY", "0"),
        ("CELERY_RECORDING_WORKER_CONCURRENCY", "invalid"),
        ("CELERY_OUTBOX_WORKER_CONCURRENCY", "0"),
    ),
)
def test_celery_numeric_parameters_fail_fast(monkeypatch, key, value):
    _clear_celery_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match=key):
        Config()


def test_celery_app_uses_durable_json_queues_and_reliable_delivery(monkeypatch):
    from tasks.celery_app import create_celery_app, worker_concurrency_limits

    _clear_celery_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "CELERY_BROKER_URL",
        "amqp://deepscout:secret@mq.internal:5672/deepscout",
    )
    config = Config()

    app = create_celery_app(config)
    queues = {queue.name: queue for queue in app.conf.task_queues}

    assert set(queues) == {
        "deepscout.cold",
        "deepscout.recording",
        "deepscout.outbox",
    }
    assert all(queue.durable and queue.exchange.durable for queue in queues.values())
    assert app.conf.task_default_delivery_mode == 2
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.broker_transport_options == {
        "confirm_publish": True,
        "max_retries": 3,
    }
    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.result_backend is None
    assert app.conf.task_routes["tasks.interview_tasks.*"]["queue"] == "deepscout.cold"
    assert (
        app.conf.task_routes["tasks.recording_tasks.*"]["queue"]
        == "deepscout.recording"
    )
    assert (
        app.conf.task_routes["tasks.outbox_dispatcher.*"]["queue"]
        == "deepscout.outbox"
    )
    assert worker_concurrency_limits(config) == {
        "cold": 2,
        "recording": 2,
        "outbox": 1,
    }


def test_rabbitmq_persistent_sample_publish_and_consume(monkeypatch):
    broker_url = os.getenv("CELERY_BROKER_TEST_URL")
    if not broker_url:
        pytest.skip("CELERY_BROKER_TEST_URL is required for RabbitMQ integration")

    from kombu import Exchange, Producer, Queue
    from tasks.celery_app import create_celery_app

    _clear_celery_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CELERY_BROKER_URL", broker_url)
    app = create_celery_app(Config())
    suffix = uuid4().hex
    exchange = Exchange(f"deepscout.acceptance.{suffix}", durable=True)
    queue = Queue(
        f"deepscout.acceptance.{suffix}",
        exchange=exchange,
        routing_key="sample",
        durable=True,
    )

    with app.connection_for_write() as connection:
        channel = connection.channel()
        bound_queue = queue(channel)
        bound_queue.declare()
        try:
            Producer(channel).publish(
                {"schema_version": 1, "sample_id": suffix},
                exchange=exchange,
                routing_key="sample",
                serializer="json",
                delivery_mode=2,
                declare=[queue],
                retry=False,
            )
            message = bound_queue.get(no_ack=False)
            assert message is not None
            assert message.payload == {"schema_version": 1, "sample_id": suffix}
            assert message.properties["delivery_mode"] == 2
            message.ack()
        finally:
            bound_queue.delete(if_unused=False, if_empty=False)
            exchange(channel).delete()
