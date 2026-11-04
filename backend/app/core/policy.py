"""Role-based access control for videos."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Video, User


def can(actor: User | None, action: str, video: Video | None = None) -> bool:
    """Return True if *actor* may perform *action* on *video*.

    Actions correspond to the RBAC matrix in the design doc.
    """
    if actor is None:
        # Anonymous viewer
        if action in ("view_public", "view_unlisted"):
            return True
        return False
    if actor.role == "admin":
        if action == "disable_user":
            return True
        if action in ("takedown", "restore") and video is not None:
            return True
        if action == "view_removed":
            return True
    if actor.role == "user":
        if video is None:
            if action in ("view_public", "view_unlisted"):
                return True
            return False
        if action == "view_private":
            return video.owner_id == actor.id
        if action == "view_public":
            return video.visibility in ("public", "unlisted") and video.removed_at is None
        if action == "view_unlisted":
            return video.visibility == "unlisted" and video.removed_at is None
        if action == "view_own":
            return video.owner_id == actor.id
        if action == "edit_own":
            return video.owner_id == actor.id
        if action == "delete_own":
            return video.owner_id == actor.id
        if action == "delete_any":
            return False
    return False