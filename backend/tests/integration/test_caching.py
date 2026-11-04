"""Phase 5: cache-aside with write-through invalidation, write-behind views, upload limits."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.engines import Database
from app.worker.runtime import close_resources
from app.worker.tasks.maintenance import flush_view_counters
from tests.integration.conftest import (
    Account,
    insert_video,
    make_client,
    register_and_login,
)
from tests.integration.test_replication import replica_replay_paused


@pytest.fixture
async def owner(app: FastAPI) -> AsyncIterator[tuple[AsyncClient, Account]]:
    client, account = await register_and_login(app, "owner@example.com")
    try:
        yield client, account
    finally:
        await client.aclose()


@pytest.fixture
async def anonymous(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with make_client(app) as client:
        yield client


async def test_video_metadata_is_cached(
    db: Database, anonymous: AsyncClient, owner: tuple[AsyncClient, Account]
) -> None:
    public_id = await insert_video(db, owner[1].id)
    first = await anonymous.get(f"/api/videos/{public_id}")
    second = await anonymous.get(f"/api/videos/{public_id}")
    assert (first.headers["x-cache"], second.headers["x-cache"]) == ("MISS", "HIT")
    assert first.json() == second.json()


async def test_feed_head_is_cached_and_deeper_pages_are_not(
    db: Database, anonymous: AsyncClient, owner: tuple[AsyncClient, Account]
) -> None:
    for title in ("one", "two", "three"):
        await insert_video(db, owner[1].id, title=title)
    first = await anonymous.get("/api/feed", params={"limit": 2})
    again = await anonymous.get("/api/feed", params={"limit": 1})
    assert (first.headers["x-cache"], again.headers["x-cache"]) == ("MISS", "HIT")
    assert [item["title"] for item in first.json()["items"]] == ["three", "two"]
    assert [item["title"] for item in again.json()["items"]] == ["three"]

    deeper = await anonymous.get("/api/feed", params={"cursor": first.json()["next_cursor"]})
    assert deeper.headers["x-cache"] == "BYPASS"
    assert [item["title"] for item in deeper.json()["items"]] == ["one"]
    assert deeper.json()["next_cursor"] is None


async def test_writes_replace_cached_data_even_while_the_replica_lags(
    db: Database, anonymous: AsyncClient, owner: tuple[AsyncClient, Account]
) -> None:
    """A stale replica read must not put the old row back in the cache after a write."""
    owner_client, account = owner
    public_id = await insert_video(db, account.id, title="Before")
    assert (await anonymous.get(f"/api/videos/{public_id}")).json()["title"] == "Before"
    assert len((await anonymous.get("/api/feed")).json()["items"]) == 1

    async with replica_replay_paused(db):
        changed = await owner_client.patch(
            f"/api/videos/{public_id}", json={"title": "After", "visibility": "unlisted"}
        )
        assert changed.status_code == 200
        # The anonymous viewer reads through the cache, which the write refreshed
        # from the primary; the paused replica still has the old row.
        video = await anonymous.get(f"/api/videos/{public_id}")
        assert video.headers["x-cache"] == "HIT"
        assert video.json()["title"] == "After"
        feed = await anonymous.get("/api/feed")
        assert feed.headers["x-cache"] == "HIT"
        assert feed.json()["items"] == []


async def test_views_count_at_once_and_reach_postgres_on_flush(
    db: Database, redis: Redis, anonymous: AsyncClient, owner: tuple[AsyncClient, Account]
) -> None:
    public_id = await insert_video(db, owner[1].id)
    assert (await anonymous.post(f"/api/videos/{public_id}/view")).json() == {"counted": True}
    # Deduplicated per viewer for an hour.
    assert (await anonymous.post(f"/api/videos/{public_id}/view")).json() == {"counted": False}
    assert (await owner[0].post(f"/api/videos/{public_id}/view")).json() == {"counted": True}

    # The count shows immediately, from Redis, before Postgres knows about it.
    assert (await anonymous.get(f"/api/videos/{public_id}")).json()["view_count"] == 2
    feed = (await anonymous.get("/api/feed")).json()["items"]
    assert feed[0]["view_count"] == 2

    try:
        await flush_view_counters()
    finally:
        await close_resources()

    async with db.write_engine.connect() as conn:
        stored = await conn.scalar(
            text("SELECT view_count FROM videos WHERE public_id = :id"), {"id": public_id}
        )
    assert stored == 2
    assert await redis.scard("views:dirty") == 0
    # The cached record was dropped by the flush, so the total is still right.
    video = await anonymous.get(f"/api/videos/{public_id}")
    assert video.headers["x-cache"] == "MISS"
    assert video.json()["view_count"] == 2


async def test_views_are_not_counted_for_videos_the_viewer_cannot_see(
    db: Database, anonymous: AsyncClient, owner: tuple[AsyncClient, Account]
) -> None:
    private_id = await insert_video(db, owner[1].id, visibility="private")
    processing_id = await insert_video(db, owner[1].id, status="processing")
    assert (await anonymous.post(f"/api/videos/{private_id}/view")).status_code == 404
    assert (await owner[0].post(f"/api/videos/{processing_id}/view")).status_code == 404


async def test_uploads_are_limited_per_user(
    app: FastAPI, owner: tuple[AsyncClient, Account]
) -> None:
    settings = get_settings().model_copy(update={"upload_limit_per_user": 2})
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        body = {"title": "Clip", "size_bytes": 10, "content_type": "video/mp4"}
        statuses = [(await owner[0].post("/api/videos", json=body)).status_code for _ in range(3)]
    finally:
        app.dependency_overrides.clear()
    assert statuses == [201, 201, 429]


async def test_admin_system_panel_reports_cache_and_queue_numbers(
    app: FastAPI, db: Database, anonymous: AsyncClient
) -> None:
    admin, account = await register_and_login(app, "admin@example.com", admin=True)
    try:
        await insert_video(db, account.id)
        await anonymous.get("/api/feed")
        await anonymous.get("/api/feed")

        system = (await admin.get("/api/admin/system")).json()
    finally:
        await admin.aclose()
    assert system["cache"]["feed"] == {"hits": 1, "misses": 1, "hit_ratio": 0.5}
    assert system["videos_by_status"]["ready"] == 1
    assert system["removed_videos"] == 0
    assert system["queues"]["error"] is None
    names = [queue["name"] for queue in system["queues"]["queues"]]
    assert names == ["videio.tasks", "videio.tasks.delay", "transcode.dlq"]
