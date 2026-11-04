"""Phase 4: owner edits and deletes, admin takedown and disable, and the edge check."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from app.db.engines import Database
from app.services.storage import S3Storage, StorageVerificationError
from tests.integration.conftest import (
    Account,
    insert_video,
    make_client,
    register_and_login,
    wait_for_replica,
)


@dataclass
class Cast:
    owner: AsyncClient
    owner_account: Account
    other: AsyncClient
    admin: AsyncClient
    admin_account: Account
    anonymous: AsyncClient


@pytest.fixture
async def cast(app: FastAPI) -> AsyncIterator[Cast]:
    owner, owner_account = await register_and_login(app, "owner@example.com", display_name="Olu")
    other, _ = await register_and_login(app, "other@example.com", display_name="Bea")
    admin, admin_account = await register_and_login(app, "admin@example.com", admin=True)
    anonymous = make_client(app)
    try:
        yield Cast(owner, owner_account, other, admin, admin_account, anonymous)
    finally:
        for client in (owner, other, admin, anonymous):
            await client.aclose()


async def test_owner_changes_visibility_and_others_lose_access(cast: Cast, db: Database) -> None:
    public_id = await insert_video(db, cast.owner_account.id)

    updated = await cast.owner.patch(
        f"/api/videos/{public_id}",
        json={"visibility": "private", "title": "  Renamed  "},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["visibility"] == "private"
    assert updated.json()["title"] == "Renamed"

    for client in (cast.anonymous, cast.other):
        assert (await client.get(f"/api/videos/{public_id}")).status_code == 404
    assert (await cast.owner.get(f"/api/videos/{public_id}")).status_code == 200
    assert (await cast.admin.get(f"/api/videos/{public_id}")).status_code == 200


async def test_only_the_owner_can_edit(cast: Cast, db: Database) -> None:
    public_id = await insert_video(db, cast.owner_account.id)
    private_id = await insert_video(db, cast.owner_account.id, visibility="private")
    body = {"title": "Mine now"}

    assert (await cast.anonymous.patch(f"/api/videos/{public_id}", json=body)).status_code == 401
    # Visible but not theirs: 403. Not even visible: 404, so private ids do not leak.
    forbidden = await cast.other.patch(f"/api/videos/{public_id}", json=body)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "FORBIDDEN"
    assert (await cast.other.patch(f"/api/videos/{private_id}", json=body)).status_code == 404
    # Admins moderate with takedowns; they do not rewrite other people's videos.
    assert (await cast.admin.patch(f"/api/videos/{public_id}", json=body)).status_code == 403


@pytest.mark.parametrize(
    "body",
    [{}, {"visibility": "secret"}, {"title": ""}, {"title": None}, {"status": "ready"}],
)
async def test_edits_are_validated(cast: Cast, db: Database, body: dict[str, object]) -> None:
    public_id = await insert_video(db, cast.owner_account.id)
    response = await cast.owner.patch(f"/api/videos/{public_id}", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_owner_delete_removes_the_row_and_every_object(
    cast: Cast, db: Database, app: FastAPI, tmp_path: Path
) -> None:
    created = await cast.owner.post(
        "/api/videos",
        json={"title": "Doomed", "size_bytes": 5, "content_type": "video/mp4"},
    )
    upload = created.json()
    async with httpx.AsyncClient() as storage_client:
        part = await storage_client.put(upload["parts"][0]["url"], content=b"bytes")
    completed = await cast.owner.post(
        f"/api/videos/{upload['public_id']}/complete",
        json={"parts": [{"part_number": 1, "etag": part.headers["etag"]}]},
    )
    assert completed.status_code == 202, completed.text

    storage: S3Storage = app.state.storage
    generated = tmp_path / "index.m3u8"
    generated.write_text("#EXTM3U\n")
    for name in ("master.m3u8", "360p/index.m3u8"):
        await storage.upload_media_file(
            generated, f"videos/{upload['public_id']}/{name}", "application/vnd.apple.mpegurl"
        )

    assert (await cast.other.delete(f"/api/videos/{upload['public_id']}")).status_code == 404
    deleted = await cast.owner.delete(f"/api/videos/{upload['public_id']}")
    assert deleted.status_code == 204, deleted.text

    assert (await cast.owner.get(f"/api/videos/{upload['public_id']}")).status_code == 404
    assert (await cast.owner.get("/api/me/videos")).json()["items"] == []
    assert await storage.delete_media_prefix(f"videos/{upload['public_id']}/") == 0
    with pytest.raises(StorageVerificationError):
        await storage.object_size(f"originals/{upload['public_id']}")
    async with db.write_engine.connect() as conn:
        rows = await conn.scalar(
            text("SELECT count(*) FROM videos WHERE public_id = :id"), {"id": upload["public_id"]}
        )
    assert rows == 0


async def test_takedown_hides_the_video_and_tells_the_owner_why(cast: Cast, db: Database) -> None:
    public_id = await insert_video(db, cast.owner_account.id, title="Contested")
    reason = "Copyright claim from the original creator"

    assert (
        await cast.owner.post(f"/api/admin/videos/{public_id}/takedown", json={"reason": reason})
    ).status_code == 403
    missing_reason = await cast.admin.post(
        f"/api/admin/videos/{public_id}/takedown", json={"reason": " "}
    )
    assert missing_reason.status_code == 422

    taken = await cast.admin.post(
        f"/api/admin/videos/{public_id}/takedown", json={"reason": reason}
    )
    assert taken.status_code == 200, taken.text
    assert taken.json()["removal_reason"] == reason
    again = await cast.admin.post(
        f"/api/admin/videos/{public_id}/takedown", json={"reason": reason}
    )
    assert again.status_code == 409

    # Gone for everyone else, a notice with the reason for the owner, full view for admins.
    feed = await cast.anonymous.get("/api/feed")
    assert public_id not in {item["public_id"] for item in feed.json()["items"]}
    assert (await cast.anonymous.get(f"/api/videos/{public_id}")).status_code == 404
    assert (await cast.other.get(f"/api/videos/{public_id}")).status_code == 404
    notice = await cast.owner.get(f"/api/videos/{public_id}")
    assert notice.status_code == 410
    assert notice.json()["error"] == {
        "code": "VIDEO_REMOVED",
        "message": "An administrator removed this video.",
        "reason": reason,
    }
    assert (await cast.admin.get(f"/api/videos/{public_id}")).json()["removed_at"]
    mine = (await cast.owner.get("/api/me/videos")).json()["items"]
    assert mine[0]["removal_reason"] == reason

    # Frozen while removed, but the owner may still delete it.
    edit = await cast.owner.patch(f"/api/videos/{public_id}", json={"title": "Fixed"})
    assert edit.status_code == 410

    restored = await cast.admin.post(
        f"/api/admin/videos/{public_id}/restore", json={"reason": "Claim withdrawn"}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["removed_at"] is None
    assert (await cast.anonymous.get(f"/api/videos/{public_id}")).status_code == 200
    assert (
        await cast.admin.post(
            f"/api/admin/videos/{public_id}/restore", json={"reason": "Claim withdrawn"}
        )
    ).status_code == 409

    await wait_for_replica(db)
    audit = (await cast.admin.get("/api/admin/audit")).json()
    assert [(entry["action"], entry["target_id"], entry["reason"]) for entry in audit] == [
        ("video.restore", public_id, "Claim withdrawn"),
        ("video.takedown", public_id, reason),
    ]
    assert audit[0]["actor_name"] == "Ann"


async def test_disabling_a_user_ends_their_sessions(cast: Cast, db: Database) -> None:
    owner_id = cast.owner_account.id
    assert (
        await cast.other.post(f"/api/admin/users/{owner_id}/disable", json={"reason": "Spam"})
    ).status_code == 403
    assert (
        await cast.admin.post(
            f"/api/admin/users/{cast.admin_account.id}/disable", json={"reason": "Oops"}
        )
    ).status_code == 400

    disabled = await cast.admin.post(
        f"/api/admin/users/{owner_id}/disable", json={"reason": "Spam uploads"}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["disabled_at"] is not None

    await wait_for_replica(db)
    assert (await cast.owner.get("/api/me")).status_code == 403
    assert (await cast.owner.post("/api/auth/refresh")).status_code == 401
    again = await cast.admin.post(
        f"/api/admin/users/{owner_id}/disable", json={"reason": "Spam uploads"}
    )
    assert again.status_code == 409
    audit = (await cast.admin.get("/api/admin/audit")).json()
    assert audit[0]["action"] == "user.disable"
    assert audit[0]["target_id"] == str(owner_id)


async def test_admin_video_list_shows_everything(cast: Cast, db: Database) -> None:
    await insert_video(db, cast.owner_account.id, visibility="private", status="processing")
    removed_id = await insert_video(db, cast.owner_account.id)
    await cast.admin.post(f"/api/admin/videos/{removed_id}/takedown", json={"reason": "Spam"})
    await wait_for_replica(db)

    everything = await cast.admin.get("/api/admin/videos")
    assert everything.status_code == 200
    assert len(everything.json()["items"]) == 2
    assert everything.json()["items"][0]["owner_email"] == "owner@example.com"
    removed = (await cast.admin.get("/api/admin/videos?removed=true")).json()["items"]
    assert [item["public_id"] for item in removed] == [removed_id]
    assert (await cast.owner.get("/api/admin/videos")).status_code == 403


@pytest.mark.parametrize(
    ("visibility", "viewer", "expected", "cacheable"),
    [
        ("public", "anonymous", 204, "1"),
        ("unlisted", "anonymous", 204, "1"),
        ("private", "anonymous", 403, None),
        ("private", "other", 403, None),
        ("private", "owner", 204, "0"),
        ("private", "admin", 204, "0"),
    ],
)
async def test_edge_authz_follows_the_policy(
    cast: Cast,
    db: Database,
    visibility: str,
    viewer: str,
    expected: int,
    cacheable: str | None,
) -> None:
    public_id = await insert_video(db, cast.owner_account.id, visibility=visibility)
    client = {
        "anonymous": cast.anonymous,
        "other": cast.other,
        "owner": cast.owner,
        "admin": cast.admin,
    }[viewer]
    response = await client.get("/internal/authz", params={"video": public_id})
    assert response.status_code == expected
    assert response.headers.get("x-media-cacheable") == cacheable


async def test_edge_authz_refuses_removed_and_unknown_videos(cast: Cast, db: Database) -> None:
    public_id = await insert_video(db, cast.owner_account.id)
    await cast.admin.post(f"/api/admin/videos/{public_id}/takedown", json={"reason": "Spam"})
    response = await cast.owner.get("/internal/authz", params={"video": public_id})
    assert response.status_code == 403
    for video in ("doesNotExst", "", "../../etc"):
        assert (
            await cast.anonymous.get("/internal/authz", params={"video": video})
        ).status_code == 403
