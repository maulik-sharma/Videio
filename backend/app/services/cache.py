"""Cache-aside for video metadata and public feed."""

from __future__ import annotations

import json

from redis.asyncio import Redis

CACHE_TTL_VIDEO_META = 300  # 5 minutes
CACHE_TTL_FEED_PAGE = 30  # 30 seconds
CACHE_TTL_THUMBNAIL = 86400  # 1 day


class CacheService:
    """Cache-aside operations using Redis."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def cache_video_meta(self, video_id: int, data: dict) -> None:
        """Cache video metadata for 5 minutes."""
        key = f"video:{video_id}"
        payload = json.dumps(data, default=str)
        await self.redis.set(key, payload, ex=CACHE_TTL_VIDEO_META)

    async def get_cached_video_meta(self, video_id: int) -> dict | None:
        """Retrieve cached video metadata. Returns None if not cached."""
        key = f"video:{video_id}"
        payload = await self.redis.get(key)
        if payload is None:
            return None
        return json.loads(payload)

    async def invalidate_video_meta(self, video_id: int) -> None:
        """Invalidate cached video metadata on write."""
        key = f"video:{video_id}"
        await self.redis.delete(key)

    async def cache_feed_page(self, page_num: int, data: list, cursor: str | None) -> None:
        """Cache public feed page for 30 seconds."""
        key = f"feed:page:{page_num}"
        payload = json.dumps({"items": data, "cursor": cursor})
        await self.redis.set(key, payload, ex=CACHE_TTL_FEED_PAGE)

    async def get_cached_feed_page(self, page_num: int) -> tuple[list | None, str | None]:
        """Retrieve cached public feed page. Returns (items, cursor) or (None, None)."""
        key = f"feed:page:{page_num}"
        payload = await self.redis.get(key)
        if payload is None:
            return None, None
        data = json.loads(payload)
        return data.get("items"), data.get("cursor")

    async def invalidate_feed_page(self, page_num: int) -> None:
        """Invalidate cached feed page on write."""
        key = f"feed:page:{page_num}"
        await self.redis.delete(key)

    async def increment_view_counter(self, video_id: int, viewer_id: str | None, ip: str) -> bool:
        """Count a view with dedupe. Returns True if this view was counted."""
        marker = f"viewed:{video_id}:{viewer_id or ip}"
        counted = await self.redis.set(marker, "1", nx=True, ex=60 * 60)
        if counted:
            await self.redis.incr(f"views:{video_id}")
            await self.redis.sadd("views:dirty", video_id)
        return counted

    async def get_cache_status_header(self) -> str:
        """Return the X-Cache header value format."""
        return "HIT/MISS"


# Global cache service instance (set during app startup)
cache_service: CacheService | None = None


def set_cache_service(redis: Redis) -> None:
    global cache_service
    cache_service = CacheService(redis)
