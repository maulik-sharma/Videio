"""Who may do what to a video: one pure function, `can`.

Every route that touches a video and the edge's `/internal/authz` check ask
`can(actor, action, video)` instead of repeating ownership and visibility rules.
It reads only plain attributes, so it works with ORM rows, cached records and
test doubles alike, and it never touches the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Protocol

Action = Literal[
    "view",  # metadata and playback
    "edit",  # title, description, visibility
    "delete",  # the video and its stored objects
    "takedown",  # admin removal, with a reason
    "restore",  # undo a takedown
    "disable_user",
]


class Actor(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def role(self) -> str: ...


class VideoLike(Protocol):
    @property
    def owner_id(self) -> uuid.UUID: ...

    @property
    def visibility(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def removed_at(self) -> datetime | None: ...


def is_admin(actor: Actor | None) -> bool:
    return actor is not None and actor.role == "admin"


def is_owner(actor: Actor | None, video: VideoLike) -> bool:
    return actor is not None and actor.id == video.owner_id


def can(actor: Actor | None, action: Action, video: VideoLike | None = None) -> bool:
    """True if `actor` (None for anonymous) may perform `action` on `video`.

    `disable_user` takes no video. Every other action needs one; asking about a
    missing video is a programming error, so it raises rather than returning False.
    """
    if action == "disable_user":
        return is_admin(actor)
    if video is None:
        raise ValueError(f"action {action!r} needs a video")

    admin = is_admin(actor)
    owner = is_owner(actor, video)
    removed = video.removed_at is not None

    if action == "view":
        if removed:
            # The owner gets a "removed" notice from the API instead of the video.
            return admin
        if admin or owner:
            return True
        # Unlisted is playable by anyone with the link; it is only left out of the feed.
        return video.status == "ready" and video.visibility in ("public", "unlisted")
    if action == "edit":
        # A removed video is frozen until an admin restores it.
        return owner and not removed
    if action == "delete":
        # Owners can always delete their own uploads, removed or not. Admins take
        # videos down instead, which keeps the evidence and can be undone.
        return owner
    if action == "takedown":
        return admin and not removed
    if action == "restore":
        return admin and removed
    raise ValueError(f"unknown action {action!r}")
