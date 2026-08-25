"""DeepScout 持久任务的 Celery 应用与 RabbitMQ 队列配置。"""

import ssl

from celery import Celery
from kombu import Exchange, Queue

from config import Config, settings

EXCHANGE_NAME = "deepscout.jobs"
COLD_QUEUE = "deepscout.cold"
RECORDING_QUEUE = "deepscout.recording"
OUTBOX_QUEUE = "deepscout.outbox"


def _broker_ssl_options(config: Config) -> bool | dict[str, object]:
    if not config.CELERY_BROKER_TLS:
        return False
    if not (
        config.CELERY_BROKER_CA_CERT
        or config.CELERY_BROKER_CLIENT_CERT
        or config.CELERY_BROKER_CLIENT_KEY
    ):
        return True
    options: dict[str, object] = {"cert_reqs": ssl.CERT_REQUIRED}
    if config.CELERY_BROKER_CA_CERT:
        options["ca_certs"] = config.CELERY_BROKER_CA_CERT
    if config.CELERY_BROKER_CLIENT_CERT:
        options["certfile"] = config.CELERY_BROKER_CLIENT_CERT
        options["keyfile"] = config.CELERY_BROKER_CLIENT_KEY
    return options


def worker_concurrency_limits(config: Config) -> dict[str, int]:
    """返回各独立 worker 角色的并发上限。"""
    return {
        "cold": config.CELERY_COLD_WORKER_CONCURRENCY,
        "recording": config.CELERY_RECORDING_WORKER_CONCURRENCY,
        "outbox": config.CELERY_OUTBOX_WORKER_CONCURRENCY,
    }


def create_celery_app(config: Config) -> Celery:
    """根据已校验配置创建不使用 result backend 的 Celery 应用。"""
    app = Celery("deepscout", broker=config.CELERY_BROKER_URL)
    exchange = Exchange(EXCHANGE_NAME, type="direct", durable=True)
    app.conf.update(
        broker_url=config.CELERY_BROKER_URL,
        broker_use_ssl=_broker_ssl_options(config),
        broker_connection_timeout=config.CELERY_BROKER_CONNECTION_TIMEOUT,
        broker_connection_retry=True,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=config.CELERY_BROKER_MAX_RETRIES,
        broker_transport_options={
            "confirm_publish": True,
            "max_retries": config.CELERY_BROKER_MAX_RETRIES,
        },
        task_publish_retry=True,
        task_publish_retry_policy={
            "max_retries": config.CELERY_BROKER_MAX_RETRIES,
            "interval_start": 0,
            "interval_step": 1,
            "interval_max": 3,
        },
        task_queues=(
            Queue(COLD_QUEUE, exchange=exchange, routing_key="cold", durable=True),
            Queue(
                RECORDING_QUEUE,
                exchange=exchange,
                routing_key="recording",
                durable=True,
            ),
            Queue(
                OUTBOX_QUEUE,
                exchange=exchange,
                routing_key="outbox",
                durable=True,
            ),
        ),
        task_routes={
            "tasks.interview_tasks.*": {
                "queue": COLD_QUEUE,
                "routing_key": "cold",
            },
            "tasks.recording_tasks.*": {
                "queue": RECORDING_QUEUE,
                "routing_key": "recording",
            },
            "tasks.outbox_dispatcher.*": {
                "queue": OUTBOX_QUEUE,
                "routing_key": "outbox",
            },
        },
        beat_schedule={
            "dispatch-pending-outbox": {
                "task": "tasks.outbox_dispatcher.dispatch_pending",
                "schedule": 1.0,
                "options": {"queue": OUTBOX_QUEUE, "routing_key": "outbox"},
            },
        },
        imports=("tasks.outbox_dispatcher",),
        task_create_missing_queues=False,
        task_default_queue=COLD_QUEUE,
        task_default_delivery_mode=2,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
    )
    return app


celery_app = create_celery_app(settings)
