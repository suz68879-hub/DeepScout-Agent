"""add RTC fencing token

Revision ID: 20260825_0004
Revises: 20260825_0003
Create Date: 2026-08-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0004"
down_revision: Union[str, Sequence[str], None] = "20260825_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "interview_session",
        sa.Column(
            "rtc_fencing_token",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("interview_session", "rtc_fencing_token")
