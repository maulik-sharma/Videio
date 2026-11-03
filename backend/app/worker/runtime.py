"""Lazy resources shared by tasks within one worker process."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.db.engines import Database
from app.services.storage import S3Storage


@dataclass
class WorkerResources:
    settings: Settings
    db: Database
    redis: Redis
    storage: S3Storage


_resources: WorkerResources | None = None


async def get_resources() -> WorkerResources:
    global _resources
    if _resources is None:
        settings = get_settings()
        _resources = WorkerResources(
            settings=settings,
            db=Database.from_settings(settings),
            redis=Redis.from_url(settings.redis_url, decode_responses=True),
            storage=S3Storage.from_settings(settings),
        )
    return _resources


async def close_resources() -> None:
    global _resources
    if _resources is None:
        return
    await _resources.redis.aclose()
    await _resources.db.dispose()
    _resources = None
