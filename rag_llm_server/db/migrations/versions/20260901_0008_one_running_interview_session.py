"""one running interview session per user

Revision ID: 20260901_0008
Revises: 20260826_0007
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0008"
down_revision: Union[str, Sequence[str], None] = "20260826_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("LOCK TABLE interview_session IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        UPDATE interview_session
        SET status = 'abandoned', ended_at = COALESCE(ended_at, NOW())
        WHERE status = 'running'
          AND id NOT IN (
            SELECT id FROM (
              SELECT DISTINCT ON (user_id) id
              FROM interview_session
              WHERE status = 'running'
              ORDER BY user_id, started_at DESC, id DESC
            ) newest
          )
        """
    )
    op.create_index(
        "uq_interview_session_one_running_per_user",
        "interview_session",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_interview_session_one_running_per_user",
        table_name="interview_session",
    )
