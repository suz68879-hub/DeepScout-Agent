"""create background job and outbox tables

Revision ID: 20260825_0005
Revises: 20260825_0004
Create Date: 2026-08-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260825_0005"
down_revision: Union[str, Sequence[str], None] = "20260825_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "background_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "attempt >= 0 AND max_attempts BETWEEN 1 AND 5 AND attempt <= max_attempts",
            name=op.f("ck_background_job_attempt_range"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name=op.f("ck_background_job_error_code_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload_ref) = 'object' "
            "AND payload_ref -> 'schema_version' = '1'::jsonb "
            "AND payload_ref - ARRAY['schema_version', 'session_id', 'recording_id', "
            "'tos_key', 'step', 'source_job_id']::text[] = '{}'::jsonb "
            "AND octet_length(payload_ref::text) <= 4096",
            name=op.f("ck_background_job_payload_ref_allowed"),
        ),
        sa.CheckConstraint(
            "result_ref IS NULL OR ("
            "jsonb_typeof(result_ref) = 'object' "
            "AND result_ref -> 'schema_version' = '1'::jsonb "
            "AND result_ref - ARRAY['schema_version', 'session_id', 'recording_id', "
            "'report_id', 'object_key']::text[] = '{}'::jsonb "
            "AND octet_length(result_ref::text) <= 4096)",
            name=op.f("ck_background_job_result_ref_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_background_job_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name="fk_background_job_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_job")),
    )
    op.create_index(
        "ix_background_job_owner_created_id",
        "background_job",
        ["owner_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_background_job_pending_scan",
        "background_job",
        ["job_type", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_background_job_running_lease",
        "background_job",
        ["lease_expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_background_job_owner_type_idempotency",
        "background_job",
        ["owner_id", "job_type", "idempotency_key"],
        unique=True,
    )

    op.create_table(
        "outbox_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "aggregate_type = 'background_job'",
            name=op.f("ck_outbox_event_aggregate_type_allowed"),
        ),
        sa.CheckConstraint(
            "attempt >= 0",
            name=op.f("ck_outbox_event_attempt_nonnegative"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object' "
            "AND payload -> 'schema_version' = '1'::jsonb "
            "AND jsonb_typeof(payload -> 'job_id') = 'string' "
            "AND payload ->> 'job_id' = aggregate_id::text "
            "AND jsonb_typeof(payload -> 'job_type') = 'string' "
            "AND payload - ARRAY['schema_version', 'job_id', 'job_type']::text[] "
            "= '{}'::jsonb "
            "AND octet_length(payload::text) <= 4096",
            name=op.f("ck_outbox_event_payload_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["aggregate_id"],
            ["background_job.id"],
            name="fk_outbox_event_aggregate_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_event")),
        sa.UniqueConstraint(
            "aggregate_id",
            "event_type",
            name=op.f("uq_outbox_event_aggregate_event"),
        ),
    )
    op.create_index(
        "ix_outbox_event_unpublished_scan",
        "outbox_event",
        ["next_attempt_at", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON background_job, outbox_event "
        "TO deepscout_app"
    )
    op.execute(
        "GRANT SELECT ON background_job, outbox_event TO deepscout_analytics"
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_event_unpublished_scan", table_name="outbox_event")
    op.drop_table("outbox_event")
    op.drop_index(
        "uq_background_job_owner_type_idempotency", table_name="background_job"
    )
    op.drop_index("ix_background_job_running_lease", table_name="background_job")
    op.drop_index("ix_background_job_pending_scan", table_name="background_job")
    op.drop_index("ix_background_job_owner_created_id", table_name="background_job")
    op.drop_table("background_job")
