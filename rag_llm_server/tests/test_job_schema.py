import os
import uuid

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg.types.json import Jsonb
from sqlalchemy import CheckConstraint, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from db.models import Base


def _check_sql(table_name: str) -> dict[str, str]:
    table = Base.metadata.tables[table_name]
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_background_job_schema_has_bounded_reference_payloads():
    table = Base.metadata.tables["background_job"]

    assert isinstance(table.c.payload_ref.type, JSONB)
    assert isinstance(table.c.result_ref.type, JSONB)
    assert table.c.payload_ref.nullable is False
    assert table.c.max_attempts.server_default.arg.text == "5"

    for column_name in (
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "lease_expires_at",
    ):
        column = table.c[column_name]
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True

    checks = _check_sql("background_job")
    assert {
        "ck_background_job_status_allowed",
        "ck_background_job_attempt_range",
        "ck_background_job_payload_ref_allowed",
        "ck_background_job_result_ref_allowed",
        "ck_background_job_error_code_format",
    } <= checks.keys()
    assert all(
        status in checks["ck_background_job_status_allowed"]
        for status in ("pending", "running", "succeeded", "failed", "cancelled")
    )
    assert "schema_version" in checks["ck_background_job_payload_ref_allowed"]
    assert "recording_id" in checks["ck_background_job_payload_ref_allowed"]
    assert "tos_key" in checks["ck_background_job_payload_ref_allowed"]
    assert "4096" in checks["ck_background_job_payload_ref_allowed"]


def test_background_job_has_owner_queue_and_idempotency_indexes():
    table = Base.metadata.tables["background_job"]
    indexes = {index.name: index for index in table.indexes if isinstance(index, Index)}

    assert {
        "ix_background_job_owner_created_id",
        "ix_background_job_pending_scan",
        "ix_background_job_running_lease",
        "uq_background_job_owner_type_idempotency",
    } <= indexes.keys()
    assert indexes["uq_background_job_owner_type_idempotency"].unique is True
    assert tuple(
        column.name
        for column in indexes["uq_background_job_owner_type_idempotency"].columns
    ) == ("owner_id", "job_type", "idempotency_key")
    assert indexes["ix_background_job_pending_scan"].dialect_options["postgresql"][
        "where"
    ] is not None
    assert indexes["ix_background_job_running_lease"].dialect_options["postgresql"][
        "where"
    ] is not None


def test_outbox_event_schema_enforces_message_contract_and_scan_index():
    table = Base.metadata.tables["outbox_event"]

    assert isinstance(table.c.payload.type, JSONB)
    assert table.c.payload.nullable is False
    assert table.c.published_at.nullable is True
    assert table.c.next_attempt_at.nullable is False

    checks = _check_sql("outbox_event")
    assert {
        "ck_outbox_event_aggregate_type_allowed",
        "ck_outbox_event_attempt_nonnegative",
        "ck_outbox_event_payload_allowed",
    } <= checks.keys()
    payload_check = checks["ck_outbox_event_payload_allowed"]
    assert all(key in payload_check for key in ("schema_version", "job_id", "job_type"))
    assert "aggregate_id" in payload_check
    assert "4096" in payload_check

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("aggregate_id", "event_type") in unique_columns

    indexes = {index.name: index for index in table.indexes if isinstance(index, Index)}
    assert "ix_outbox_event_unpublished_scan" in indexes
    assert indexes["ix_outbox_event_unpublished_scan"].dialect_options["postgresql"][
        "where"
    ] is not None


def test_postgres_rejects_invalid_job_and_outbox_rows():
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL schema tests")
    conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    owner_id = uuid.uuid4()
    job_id = uuid.uuid4()
    event_id = uuid.uuid4()
    connection = psycopg.connect(conninfo, connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO app_user (id, username, password_hash) VALUES (%s, %s, %s)",
                (owner_id, f"p3_schema_{owner_id.hex}", "test-only"),
            )

            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                cursor.execute(
                    """
                    INSERT INTO background_job
                        (id, owner_id, job_type, status, idempotency_key, payload_ref)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(),
                        owner_id,
                        "interview.finish",
                        "unknown",
                        "invalid-status",
                        Jsonb({"schema_version": 1, "session_id": str(uuid.uuid4())}),
                    ),
                )

            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                cursor.execute(
                    """
                    INSERT INTO background_job
                        (id, owner_id, job_type, idempotency_key, payload_ref)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(),
                        owner_id,
                        "recording.process",
                        "invalid-payload",
                        Jsonb({"schema_version": 1, "access_token": "must-not-persist"}),
                    ),
                )

            cursor.execute(
                """
                INSERT INTO background_job
                    (id, owner_id, job_type, idempotency_key, payload_ref)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    owner_id,
                    "interview.finish",
                    "valid-job",
                    Jsonb({"schema_version": 1, "session_id": str(uuid.uuid4())}),
                ),
            )
            event_payload = Jsonb(
                {
                    "schema_version": 1,
                    "job_id": str(job_id),
                    "job_type": "interview.finish",
                }
            )
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                cursor.execute(
                    """
                    INSERT INTO outbox_event
                        (id, aggregate_type, aggregate_id, event_type, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(),
                        "background_job",
                        job_id,
                        "job.mismatched",
                        Jsonb(
                            {
                                "schema_version": 1,
                                "job_id": str(uuid.uuid4()),
                                "job_type": "interview.finish",
                            }
                        ),
                    ),
                )
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                cursor.execute(
                    """
                    INSERT INTO outbox_event
                        (id, aggregate_type, aggregate_id, event_type, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(),
                        "background_job",
                        job_id,
                        "job.invalid",
                        Jsonb(
                            {
                                "schema_version": 1,
                                "job_id": str(job_id),
                                "job_type": "interview.finish",
                                "recording_binary": "must-not-persist",
                            }
                        ),
                    ),
                )
            cursor.execute(
                """
                INSERT INTO outbox_event
                    (id, aggregate_type, aggregate_id, event_type, payload)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (event_id, "background_job", job_id, "job.created", event_payload),
            )
            with pytest.raises(
                psycopg.errors.UniqueViolation
            ), connection.transaction():
                cursor.execute(
                    """
                    INSERT INTO outbox_event
                        (id, aggregate_type, aggregate_id, event_type, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(),
                        "background_job",
                        job_id,
                        "job.created",
                        event_payload,
                    ),
                )
    finally:
        connection.rollback()
        connection.close()


def test_analytics_role_cannot_write_job_tables():
    load_dotenv()
    database_url = os.getenv("ANALYTICS_DATABASE_URL")
    if not database_url:
        pytest.skip("ANALYTICS_DATABASE_URL is required for PostgreSQL schema tests")
    conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    connection = psycopg.connect(conninfo, connect_timeout=5)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM background_job")
            cursor.fetchone()
            with pytest.raises(
                (
                    psycopg.errors.InsufficientPrivilege,
                    psycopg.errors.ReadOnlySqlTransaction,
                )
            ):
                cursor.execute("DELETE FROM background_job WHERE FALSE")
    finally:
        connection.rollback()
        connection.close()
