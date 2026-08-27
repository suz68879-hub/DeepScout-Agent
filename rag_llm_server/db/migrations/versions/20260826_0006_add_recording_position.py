"""persist recording target position

Revision ID: 20260826_0006
Revises: 20260825_0005
Create Date: 2026-08-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_0006"
down_revision: Union[str, Sequence[str], None] = "20260825_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recording", sa.Column("position", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recording", "position")
