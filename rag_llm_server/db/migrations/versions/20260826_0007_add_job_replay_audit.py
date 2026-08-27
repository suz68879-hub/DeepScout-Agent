"""add audited job replay chain

Revision ID: 20260826_0007
Revises: 20260826_0006
Create Date: 2026-08-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260826_0007"
down_revision: Union[str, Sequence[str], None] = "20260826_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "background_job",
        sa.Column("replay_of", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "background_job",
        sa.Column("replay_operator", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "background_job",
        sa.Column("replay_approved_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "background_job",
        sa.Column("replay_reason", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "background_job",
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_background_job_replay_audit_complete"),
        "background_job",
        "(replay_of IS NULL AND replay_operator IS NULL "
        "AND replay_approved_by IS NULL AND replay_reason IS NULL "
        "AND replayed_at IS NULL) OR "
        "(replay_of IS NOT NULL AND replay_of <> id "
        "AND char_length(replay_operator) BETWEEN 3 AND 128 "
        "AND replay_operator = btrim(replay_operator) "
        "AND replay_operator !~ '[[:cntrl:][:space:]]' "
        "AND (replay_approved_by IS NULL OR ("
        "char_length(replay_approved_by) BETWEEN 3 AND 128 "
        "AND replay_approved_by = btrim(replay_approved_by) "
        "AND replay_approved_by !~ '[[:cntrl:][:space:]]')) "
        "AND char_length(replay_reason) BETWEEN 10 AND 512 "
        "AND replay_reason = btrim(replay_reason) "
        "AND replay_reason !~ '[[:cntrl:]]' "
        "AND replayed_at IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_background_job_replay_of",
        "background_job",
        "background_job",
        ["replay_of"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_background_job_replay_of",
        "background_job",
        ["replay_of"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_background_job_replay_of", "background_job", type_="unique"
    )
    op.drop_constraint(
        "fk_background_job_replay_of", "background_job", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("ck_background_job_replay_audit_complete"),
        "background_job",
        type_="check",
    )
    op.drop_column("background_job", "replayed_at")
    op.drop_column("background_job", "replay_reason")
    op.drop_column("background_job", "replay_approved_by")
    op.drop_column("background_job", "replay_operator")
    op.drop_column("background_job", "replay_of")
