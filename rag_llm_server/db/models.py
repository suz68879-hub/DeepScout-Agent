import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
        }
    )


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    username: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'user'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("uq_app_user_username_lower", func.lower(username), unique=True),
    )


class AuthSession(Base):
    __tablename__ = "auth_session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", name="fk_auth_session_user", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_auth_session_user_expires", user_id, expires_at),)


class Resume(Base):
    __tablename__ = "resume"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", name="fk_resume_user", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str | None] = mapped_column(Text)
    structured_json: Mapped[dict | list | None] = mapped_column(JSONB)
    source: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_resume_id_user_id"),
        Index("ix_resume_user_created_id", user_id, created_at.desc(), id.desc()),
    )


class InterviewSession(Base):
    __tablename__ = "interview_session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", name="fk_interview_session_user", ondelete="CASCADE"),
        nullable=False,
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    position: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rtc_room_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rtc_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rtc_task_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rtc_callback_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rtc_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'created'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["resume_id", "user_id"],
            ["resume.id", "resume.user_id"],
            name="fk_interview_session_resume_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "user_id", name="uq_interview_session_id_user_id"),
        UniqueConstraint("rtc_room_id", name="uq_interview_session_rtc_room_id"),
        UniqueConstraint("rtc_user_id", name="uq_interview_session_rtc_user_id"),
        UniqueConstraint("rtc_task_id", name="uq_interview_session_rtc_task_id"),
        UniqueConstraint("rtc_callback_id", name="uq_interview_session_rtc_callback_id"),
        Index(
            "ix_interview_session_user_started_id",
            user_id,
            started_at.desc(),
            id.desc(),
        ),
        Index(
            "ix_interview_session_user_status_started_id",
            user_id,
            status,
            started_at.desc(),
            id.desc(),
        ),
    )


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "interview_session.id",
            name="fk_message_session",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_message_session_seq"),
        Index("ix_message_session_created_id", session_id, created_at, id),
    )


class InterviewReport(Base):
    __tablename__ = "interview_report"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", name="fk_interview_report_user", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scores_json: Mapped[dict | list | None] = mapped_column(JSONB)
    feedback_json: Mapped[dict | list | None] = mapped_column(JSONB)
    suggestions_json: Mapped[dict | list | None] = mapped_column(JSONB)
    position: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'session'"))
    md_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["interview_session.id", "interview_session.user_id"],
            name="fk_interview_report_session_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "user_id", name="uq_interview_report_id_user_id"),
        UniqueConstraint("session_id", name="uq_interview_report_session_id"),
        Index("ix_interview_report_user_created_id", user_id, created_at.desc(), id.desc()),
    )


class Recording(Base):
    __tablename__ = "recording"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", name="fk_recording_user", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str | None] = mapped_column(Text)
    ext: Mapped[str | None] = mapped_column(String(32))
    tos_key: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(32))
    asr_task_id: Mapped[str | None] = mapped_column(String(255))
    transcript_json: Mapped[dict | list | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["report_id", "user_id"],
            ["interview_report.id", "interview_report.user_id"],
            name="fk_recording_report_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_recording_user_created_id", user_id, created_at.desc(), id.desc()),
    )
