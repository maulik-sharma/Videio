"""Policy / RBAC matrix tests for Videio."""

import uuid

from app.core.policy import can


def _u():
    return uuid.uuid4()


def _user(role="user", id_=None, **extra):
    return type("User", (), {"role": role, "id": id_ or _u(), "email": f"test@{role}.com", "display_name": role, **extra})()


def _video(**kwargs):
    id_ = kwargs.pop("id", 1)
    owner_id = kwargs.pop("owner_id", None)
    return_type = type("Video", (), {
        "id": id_,
        "public_id": "3kTq9bZx1",
        "owner_id": owner_id or _u(),
        "title": "Test",
        "visibility": kwargs.get("visibility", "public"),
        "status": kwargs.get("status", "ready"),
        "removed_at": kwargs.get("removed_at"),
    })
    # If owner_id not provided, set it to the same ID as the video for owner tests
    if owner_id is None and "owner_id" not in str(return_type):
        # We'll handle this in test setup instead
        pass
    return return_type


def _video_with_owner(owner_id, **kwargs):
    return _video(owner_id=owner_id, **kwargs)


def _video_public():
    return _video(visibility="public", status="ready", removed_at=None)


def _video_unlisted():
    return _video(visibility="unlisted", status="ready", removed_at=None)


def _video_private(owner_id=None):
    return _video(visibility="private", removed_at=None, owner_id=owner_id)


def _video_removed():
    return _video(visibility="private", removed_at="2024-01-01")


def _user_admin(id_=None):
    return _user("admin", id=id_)


def _user_user(id_=None):
    return _user("user", id=id_)


def test_admin_can_disable_user():
    admin = _user_admin()
    assert can(admin, "disable_user") is True


def test_non_admin_cannot_disable_user():
    user = _user_user()
    assert can(user, "disable_user") is False


def test_owner_can_view_private():
    owner_id = _u()
    owner = _user_user(owner_id)
    video = _video_private(owner_id)
    assert can(owner, "view_private", video) is True


def test_non_owner_cannot_view_private():
    viewer_id = _u()
    owner_id = _u()
    user = _user_user(viewer_id)
    video = _video_private(owner_id)
    assert can(user, "view_private", video) is False


def test_owner_can_edit_own():
    owner_id = _u()
    owner = _user_user(owner_id)
    video = _video_private(owner_id=owner_id)
    assert can(owner, "edit_own", video) is True


def test_non_owner_cannot_edit_own():
    viewer_id = _u()
    owner_id = _u()
    user = _user_user(viewer_id)
    video = _video_private(owner_id=owner_id)
    assert can(user, "edit_own", video) is False


def test_owner_can_delete_own():
    owner_id = _u()
    owner = _user_user(owner_id)
    video = _video_private(owner_id=owner_id)
    assert can(owner, "delete_own", video) is True


def test_non_owner_cannot_delete_own():
    viewer_id = _u()
    owner_id = _u()
    user = _user_user(viewer_id)
    video = _video_private(owner_id=owner_id)
    assert can(user, "delete_own", video) is False


def test_admin_can_takedown_any():
    admin = _user_admin()
    video = _video_private()
    assert can(admin, "takedown", video) is True


def test_admin_can_restore_any():
    admin = _user_admin()
    video = _video_removed()
    assert can(admin, "restore", video) is True


def test_admin_can_view_removed():
    admin = _user_admin()
    video = _video_removed()
    assert can(admin, "view_removed", video) is True


def test_non_admin_cannot_view_removed():
    user = _user_user()
    video = _video_removed()
    assert can(user, "view_removed", video) is False


def test_anonymous_cannot_view_private():
    video = _video_private()
    assert can(None, "view_private", video) is False


def test_anonymous_can_view_public():
    video = _video_public()
    assert can(None, "view_public", video) is True


def test_anonymous_can_view_unlisted():
    video = _video_unlisted()
    assert can(None, "view_unlisted", video) is True


def test_anonymous_cannot_view_private_or_removed():
    video_private = _video_private()
    video_removed = _video_removed()
    assert can(None, "view_private", video_private) is False
    assert can(None, "view_removed", video_removed) is False