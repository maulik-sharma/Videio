"""Cache-aside for video records and the first page of the public feed.

Keys:
  video:{publicId} -> one VideoRecord as JSON, TTL 5 min. "-" marks a deleted video.
  feed:head        -> the newest public videos (enough for any page size), TTL 30 s.
  cache:stats      -> hash of hit and miss counters for the admin System panel.

Reads fill the cache with SET NX. Writes go through `publish_video_change`,
which reloads the row from the primary and overwrites the key, so a reader that
fetched a slightly stale row from the replica cannot win the race and put old
data back after a write: its NX fill fails once the fresh value is there.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, TypeAdapter
from redis.asyncio import Redis
from sqlalchemy import desc, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cursors import VideoCursor
from app.core.ids import PUBLIC_ID_LENGTH
from app.db.models import User, Video

VIDEO_TTL_S = 5 * 60
FEED_TTL_S = 30
# The API serves at most 50 videos a page; one more says whether a next page exists.
FEED_HEAD_SIZE = 51
FEED_KEY = "feed:head"
STATS_KEY = "cache:stats"
_GONE = "-"

_PUBLIC_ID = re.compile(rf"[A-Za-z0-9]{{{PUBLIC_ID_LENGTH}}}")


class VideoRecord(BaseModel):
    """Everything the API needs to answer for one video, including policy inputs."""

    id: int
    public_id: str
    owner_id: uuid.UUID
    owner_name: str
    title: str
    description: str
    visibility: str
    status: str
    size_bytes: int
    content_type: str
    duration_s: float | None
    renditions: dict[str, Any] | None
    thumbnail_key: str | None
    view_count: int
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
    removed_at: datetime | None
    removal_reason: str | None

    @classmethod
    def from_row(cls, video: Video, owner_name: str) -> VideoRecord:
        return cls(
            id=video.id,
            public_id=video.public_id,
            owner_id=video.owner_id,
            owner_name=owner_name,
            title=video.title,
            description=video.description,
            visibility=video.visibility,
            status=video.status,
            size_bytes=video.original_size_bytes,
            content_type=video.original_content_type,
            duration_s=video.duration_s,
            renditions=video.renditions,
            thumbnail_key=video.thumbnail_key,
            view_count=video.view_count,
            failure_reason=video.failure_reason,
            created_at=video.created_at,
            updated_at=video.updated_at,
            removed_at=video.removed_at,
            removal_reason=video.removal_reason,
        )


_RECORDS = TypeAdapter(list[VideoRecord])


def is_public_id(value: str) -> bool:
    return _PUBLIC_ID.fullmatch(value) is not None


def video_key(public_id: str) -> str:
    return f"video:{public_id}"


def video_ref_filter(video_ref: str) -> Any:
    """Accept public ids in URLs while tolerating internal numeric ids for APIs."""
    if video_ref.isdecimal():
        return or_(Video.id == int(video_ref), Video.public_id == video_ref)
    return Video.public_id == video_ref


def _with_owner() -> Any:
    return select(Video, User.display_name).join(User, User.id == Video.owner_id)


async def load_video_record(session: AsyncSession, video_ref: str) -> VideoRecord | None:
    row = (await session.execute(_with_owner().where(video_ref_filter(video_ref)))).first()
    if row is None:
        return None
    video, owner_name = row
    return VideoRecord.from_row(video, owner_name)


async def load_feed(
    session: AsyncSession, after: VideoCursor | None, count: int
) -> list[VideoRecord]:
    """Public, ready, not removed; newest first, by keyset. Uses the partial index."""
    statement = _with_owner().where(
        Video.visibility == "public",
        Video.status == "ready",
        Video.removed_at.is_(None),
    )
    if after is not None:
        statement = statement.where(
            tuple_(Video.created_at, Video.id) < tuple_(after.created_at, after.video_id)
        )
    statement = statement.order_by(desc(Video.created_at), desc(Video.id)).limit(count)
    rows = (await session.execute(statement)).all()
    return [VideoRecord.from_row(video, owner_name) for video, owner_name in rows]


async def _count(redis: Redis, name: str, *, hit: bool) -> None:
    await redis.hincrby(STATS_KEY, f"{name}:{'hit' if hit else 'miss'}", 1)


async def get_video_record(
    redis: Redis, session: AsyncSession, video_ref: str
) -> tuple[VideoRecord | None, bool]:
    """The video for `video_ref` (None if it does not exist) and whether it came from cache."""
    if is_public_id(video_ref):
        cached = await redis.get(video_key(video_ref))
        if cached is not None:
            await _count(redis, "video", hit=True)
            return (None if cached == _GONE else VideoRecord.model_validate_json(cached)), True

    record = await load_video_record(session, video_ref)
    await _count(redis, "video", hit=False)
    if record is not None:
        await redis.set(
            video_key(record.public_id), record.model_dump_json(), ex=VIDEO_TTL_S, nx=True
        )
    return record, False


async def get_feed_head(redis: Redis, session: AsyncSession) -> tuple[list[VideoRecord], bool]:
    """The newest FEED_HEAD_SIZE public videos and whether they came from cache."""
    cached = await redis.get(FEED_KEY)
    if cached is not None:
        await _count(redis, "feed", hit=True)
        return _RECORDS.validate_json(cached), True
    records = await load_feed(session, None, FEED_HEAD_SIZE)
    await _count(redis, "feed", hit=False)
    await redis.set(FEED_KEY, _RECORDS.dump_json(records), ex=FEED_TTL_S, nx=True)
    return records, False


async def publish_video_change(
    redis: Redis, primary: AsyncSession, public_id: str, *, feed: bool = True
) -> None:
    """Call after committing any change to a video. `primary` must read the primary.

    Overwrites the cached record with the committed row (or a tombstone if the
    video is gone) and, if the change could affect the feed, rebuilds its head.
    """
    record = await load_video_record(primary, public_id)
    value = _GONE if record is None else record.model_dump_json()
    await redis.set(video_key(public_id), value, ex=VIDEO_TTL_S)
    if feed:
        records = await load_feed(primary, None, FEED_HEAD_SIZE)
        await redis.set(FEED_KEY, _RECORDS.dump_json(records), ex=FEED_TTL_S)


async def forget_videos(redis: Redis, public_ids: list[str]) -> None:
    """Drop cached records, e.g. after the view-count flush changes many rows at once."""
    if public_ids:
        await redis.delete(*(video_key(public_id) for public_id in public_ids), FEED_KEY)


async def pending_views(redis: Redis, video_ids: list[int]) -> dict[int, int]:
    """Views counted in Redis that the scheduled flush has not moved to Postgres yet."""
    if not video_ids:
        return {}
    values = await redis.mget([f"views:{video_id}" for video_id in video_ids])
    return {
        video_id: int(value)
        for video_id, value in zip(video_ids, values, strict=True)
        if value is not None
    }


async def stats(redis: Redis) -> dict[str, dict[str, float | int | None]]:
    raw = await redis.hgetall(STATS_KEY)
    result: dict[str, dict[str, float | int | None]] = {}
    for name in ("video", "feed"):
        hits = int(raw.get(f"{name}:hit", 0))
        misses = int(raw.get(f"{name}:miss", 0))
        total = hits + misses
        result[name] = {
            "hits": hits,
            "misses": misses,
            "hit_ratio": round(hits / total, 4) if total else None,
        }
    return result
