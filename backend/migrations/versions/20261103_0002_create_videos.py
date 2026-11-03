"""Create videos and transcode attempts.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("public_id", sa.CHAR(length=11), nullable=False),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("visibility", sa.Text(), nullable=False, server_default="public"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending_upload"),
        sa.Column("original_key", sa.Text(), nullable=False),
        sa.Column("original_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("original_content_type", sa.Text(), nullable=False),
        sa.Column("multipart_upload_id", sa.Text(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("renditions", postgresql.JSONB(), nullable=True),
        sa.Column("thumbnail_key", sa.Text(), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "removed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("removal_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "visibility IN ('public', 'unlisted', 'private')", name="videos_visibility_check"
        ),
        sa.CheckConstraint(
            "status IN ('pending_upload', 'uploaded', 'processing', 'ready', 'failed')",
            name="videos_status_check",
        ),
        sa.CheckConstraint("original_size_bytes > 0", name="videos_original_size_check"),
        sa.UniqueConstraint("public_id", name="videos_public_id_key"),
    )
    op.create_index(
        "ix_videos_owner_created_at",
        "videos",
        ["owner_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_videos_public_ready",
        "videos",
        [sa.text("created_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text(
            "visibility = 'public' AND status = 'ready' AND removed_at IS NULL"
        ),
    )

    op.create_table(
        "transcode_attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "video_id",
            sa.BigInteger(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_transcode_attempts_video_id", "transcode_attempts", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_transcode_attempts_video_id", table_name="transcode_attempts")
    op.drop_table("transcode_attempts")
    op.drop_index("ix_videos_public_ready", table_name="videos")
    op.drop_index("ix_videos_owner_created_at", table_name="videos")
    op.drop_table("videos")
