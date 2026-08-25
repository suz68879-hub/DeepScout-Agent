"""create tenant analytics views

Revision ID: 20260825_0003
Revises: 20260825_0002
Create Date: 2026-08-25
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260825_0003"
down_revision: Union[str, Sequence[str], None] = "20260825_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    op.execute(
        """
        CREATE VIEW analytics.resume WITH (security_barrier=true) AS
        SELECT id, content, structured_json, source, status, created_at, updated_at
        FROM public.resume
        WHERE user_id = current_setting('app.user_id', true)::uuid
        """
    )
    op.execute(
        """
        CREATE VIEW analytics.interview_session WITH (security_barrier=true) AS
        SELECT id, resume_id, position, stage, status, started_at, ended_at
        FROM public.interview_session
        WHERE user_id = current_setting('app.user_id', true)::uuid
        """
    )
    op.execute(
        """
        CREATE VIEW analytics.message WITH (security_barrier=true) AS
        SELECT m.id, m.session_id, m.role, m.content, m.seq, m.created_at
        FROM public.message AS m
        JOIN public.interview_session AS s ON s.id = m.session_id
        WHERE s.user_id = current_setting('app.user_id', true)::uuid
        """
    )
    op.execute(
        """
        CREATE VIEW analytics.interview_report WITH (security_barrier=true) AS
        SELECT id, session_id, scores_json, feedback_json, suggestions_json,
               position, source, md_path, created_at
        FROM public.interview_report
        WHERE user_id = current_setting('app.user_id', true)::uuid
        """
    )
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM deepscout_analytics")
    op.execute("REVOKE CREATE ON SCHEMA analytics FROM PUBLIC, deepscout_analytics")
    op.execute("GRANT USAGE ON SCHEMA analytics TO deepscout_analytics")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO deepscout_analytics")


def downgrade() -> None:
    op.execute("DROP SCHEMA analytics CASCADE")
    op.execute(
        "GRANT SELECT ON TABLE public.resume, public.interview_session, public.message, "
        "public.interview_report, public.recording TO deepscout_analytics"
    )
