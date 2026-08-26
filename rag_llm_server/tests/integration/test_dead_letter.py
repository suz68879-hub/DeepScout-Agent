import os
import uuid

import pytest
from kombu import Exchange, Queue

from config import Config
from services.jobs.types import JobErrorCode
from tasks.celery_app import create_celery_app
from tasks.retry_policy import DEAD_LETTER_EXCHANGE, DeadLetterPublisher


def test_terminal_job_reaches_rabbitmq_dead_letter_without_pii(monkeypatch):
    broker_url = os.getenv("CELERY_BROKER_TEST_URL")
    if not broker_url:
        pytest.skip("CELERY_BROKER_TEST_URL is required for RabbitMQ integration")

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CELERY_BROKER_URL", broker_url)
    app = create_celery_app(Config())
    job_id = uuid.uuid4()
    exchange = Exchange(DEAD_LETTER_EXCHANGE, type="direct", durable=True)
    observer = Queue(
        f"deepscout.dlq.acceptance.{uuid.uuid4().hex}",
        exchange=exchange,
        routing_key="failed",
        durable=False,
        exclusive=True,
        auto_delete=True,
    )

    with app.connection_for_write() as connection:
        bound_queue = observer(connection.channel())
        bound_queue.declare()
        try:
            DeadLetterPublisher(app).publish(
                job_id=job_id,
                error_code=JobErrorCode.MAX_ATTEMPTS_EXCEEDED,
                original_queue="deepscout.recording",
            )
            message = bound_queue.get(no_ack=False)
            assert message is not None
            assert message.payload == {
                "job_id": str(job_id),
                "error_code": "MAX_ATTEMPTS_EXCEEDED",
                "original_queue": "deepscout.recording",
            }
            assert message.properties["delivery_mode"] == 2
            message.ack()
        finally:
            bound_queue.delete(if_unused=False, if_empty=False)
