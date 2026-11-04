"""The RBAC matrix from docs/plan.md, one parametrized case per cell."""

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from app.core.policy import Action, can

OWNER_ID = uuid.uuid4()
REMOVED_AT = datetime(2026, 11, 1, tzinfo=UTC)


@dataclass(frozen=True)
class FakeActor:
    id: uuid.UUID
    role: str


@dataclass(frozen=True)
class FakeVideo:
    owner_id: uuid.UUID = OWNER_ID
    visibility: str = "public"
    status: str = "ready"
    removed_at: datetime | None = None


ACTORS: dict[str, FakeActor | None] = {
    "anonymous": None,
    "user": FakeActor(uuid.uuid4(), "user"),
    "owner": FakeActor(OWNER_ID, "user"),
    "admin": FakeActor(uuid.uuid4(), "admin"),
}

PUBLIC = FakeVideo()
UNLISTED = FakeVideo(visibility="unlisted")
PRIVATE = FakeVideo(visibility="private")
REMOVED = FakeVideo(removed_at=REMOVED_AT)
PROCESSING = FakeVideo(status="processing")

# (action, video, actors allowed). Every actor not listed must be refused.
MATRIX: list[tuple[str, Action, FakeVideo, set[str]]] = [
    ("view public", "view", PUBLIC, {"anonymous", "user", "owner", "admin"}),
    ("view unlisted", "view", UNLISTED, {"anonymous", "user", "owner", "admin"}),
    ("view private", "view", PRIVATE, {"owner", "admin"}),
    ("view removed", "view", REMOVED, {"admin"}),
    ("view removed private", "view", replace(PRIVATE, removed_at=REMOVED_AT), {"admin"}),
    ("view not yet ready", "view", PROCESSING, {"owner", "admin"}),
    ("edit", "edit", PUBLIC, {"owner"}),
    ("edit private", "edit", PRIVATE, {"owner"}),
    ("edit removed", "edit", REMOVED, set()),
    ("delete own", "delete", PUBLIC, {"owner"}),
    ("delete own removed", "delete", REMOVED, {"owner"}),
    ("take down", "takedown", PUBLIC, {"admin"}),
    ("take down private", "takedown", PRIVATE, {"admin"}),
    ("take down twice", "takedown", REMOVED, set()),
    ("restore", "restore", REMOVED, {"admin"}),
    ("restore a live video", "restore", PUBLIC, set()),
]

CASES = [
    pytest.param(actor_name, action, video, actor_name in allowed, id=f"{label}-{actor_name}")
    for label, action, video, allowed in MATRIX
    for actor_name in ACTORS
]


@pytest.mark.parametrize(("actor_name", "action", "video", "expected"), CASES)
def test_matrix(actor_name: str, action: Action, video: FakeVideo, expected: bool) -> None:
    assert can(ACTORS[actor_name], action, video) is expected


@pytest.mark.parametrize(("actor_name", "expected"), [("admin", True), ("anonymous", False)])
def test_only_admins_disable_users(actor_name: str, expected: bool) -> None:
    assert can(ACTORS[actor_name], "disable_user") is expected
    if not expected:
        assert can(ACTORS["user"], "disable_user") is False
        assert can(ACTORS["owner"], "disable_user") is False


def test_video_actions_need_a_video() -> None:
    with pytest.raises(ValueError, match="needs a video"):
        can(ACTORS["owner"], "edit")
