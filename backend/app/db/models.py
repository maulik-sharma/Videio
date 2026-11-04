import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('user', 'admin')", name="users_role_check"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('public', 'unlisted', 'private')", name="videos_visibility_check"
        ),
        CheckConstraint(
            "status IN ('pending_upload', 'uploaded', 'processing', 'ready', 'failed')",
            name="videos_status_check",
        ),
        CheckConstraint("original_size_bytes > 0", name="videos_original_size_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    public_id: Mapped[str] = mapped_column(CHAR(11), unique=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    visibility: Mapped[str] = mapped_column(Text, server_default="public")
    status: Mapped[str] = mapped_column(Text, server_default="pending_upload")
    original_key: Mapped[str] = mapped_column(Text)
    original_size_bytes: Mapped[int] = mapped_column(BigInteger)
    original_content_type: Mapped[str] = mapped_column(Text)
    multipart_upload_id: Mapped[str | None] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column()
    renditions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    thumbnail_key: Mapped[str | None] = mapped_column(Text)
    view_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    removal_reason: Mapped[str | None] = mapped_column(Text)


Index(
    "ix_videos_owner_created_at",
    Video.owner_id,
    Video.created_at.desc(),
    Video.id.desc(),
)
Index(
    "ix_videos_public_ready",
    Video.created_at.desc(),
    Video.id.desc(),
    postgresql_where=text("visibility = 'public' AND status = 'ready' AND removed_at IS NULL"),
)


class TranscodeAttempt(Base):
    __tablename__ = "transcode_attempts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Every admin action, with who did it and why. Rows are only ever inserted."""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("target_type IN ('video', 'user')", name="audit_log_target_type_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text)
    # A video's public id or a user's uuid, so one column fits both targets.
    target_id: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_audit_log_target", AuditLog.target_type, AuditLog.target_id, AuditLog.id.desc())
