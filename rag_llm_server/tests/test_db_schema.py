from pathlib import Path

from sqlalchemy import BigInteger, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from db.models import Base


EXPECTED_TABLES = {
    "app_user",
    "auth_session",
    "resume",
    "interview_session",
    "message",
    "interview_report",
    "recording",
}


def test_metadata_contains_business_tables_and_postgres_types():
    assert set(Base.metadata.tables) == EXPECTED_TABLES

    assert isinstance(Base.metadata.tables["app_user"].c.id.type, UUID)
    assert isinstance(Base.metadata.tables["message"].c.id.type, BigInteger)
    assert isinstance(Base.metadata.tables["resume"].c.structured_json.type, JSONB)
    assert isinstance(Base.metadata.tables["interview_report"].c.scores_json.type, JSONB)
    assert isinstance(Base.metadata.tables["recording"].c.transcript_json.type, JSONB)

    for table in Base.metadata.tables.values():
        for column in table.c:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True


def test_schema_has_required_uniqueness_and_owner_indexes():
    metadata = Base.metadata
    user_indexes = {index.name: index for index in metadata.tables["app_user"].indexes}
    assert user_indexes["uq_app_user_username_lower"].unique is True
    assert "lower" in str(user_indexes["uq_app_user_username_lower"].expressions[0]).lower()

    session = metadata.tables["interview_session"]
    session_unique_names = {
        constraint.name
        for constraint in session.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert {
        "uq_interview_session_rtc_room_id",
        "uq_interview_session_rtc_user_id",
        "uq_interview_session_rtc_task_id",
        "uq_interview_session_rtc_callback_id",
    } <= session_unique_names
    assert "version" in session.c
    assert session.c.version.nullable is False

    message = metadata.tables["message"]
    message_unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in message.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("session_id", "seq") in message_unique_columns

    expected_indexes = {
        "ix_resume_user_created_id",
        "ix_interview_session_user_started_id",
        "ix_interview_report_user_created_id",
        "ix_recording_user_created_id",
    }
    actual_indexes = {
        index.name
        for table in metadata.tables.values()
        for index in table.indexes
        if isinstance(index, Index)
    }
    assert expected_indexes <= actual_indexes


def test_alembic_ini_does_not_contain_database_credentials():
    ini = (Path(__file__).parents[1] / "alembic.ini").read_text(encoding="utf-8")
    assert "sqlalchemy.url" not in ini
    assert "postgresql://" not in ini
