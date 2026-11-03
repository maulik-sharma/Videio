"""Scheduled maintenance tasks. The scheduler only enqueues these functions."""

from __future__ import annotations

from sqlalchemy import case, func, select, text, update

from app.db.models import Video
from app.worker.broker import broker
from app.worker.runtime import get_resources


async def _take_lock(name: str) -> bool:
    resources = await get_resources()
    return bool(await resources.redis.set(f"lock:{name}", "1", nx=True, px=60_000))


@broker.task(schedule=[{"cron": "* * * * *", "schedule_id": "flush_view_counters"}])
async def flush_view_counters() -> None:
    """Move Redis view increments to Postgres in one update per batch."""
    if not await _take_lock("flush_view_counters"):
        return
    resources = await get_resources()
    raw_ids = await resources.redis.smembers("views:dirty")
    if not raw_ids:
        return

    # Remove the batch before reading counters. A view arriving during this
    # window adds the id back to the dirty set for the next run.
    await resources.redis.srem("views:dirty", *raw_ids)
    counts: dict[int, int] = {}
    for raw_id in raw_ids:
        id_text = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        raw_count = await resources.redis.getdel(f"views:{id_text}")
        if raw_count is not None and int(raw_count) > 0:
            counts[int(id_text)] = int(raw_count)
    if not counts:
        return

    async with resources.db.write_sessions() as session:
        await session.execute(
            update(Video)
            .where(Video.id.in_(counts))
            .values(view_count=Video.view_count + case(counts, value=Video.id, else_=0))
        )
        await session.commit()


@broker.task(schedule=[{"cron": "*/5 * * * *", "schedule_id": "reap_stuck_transcodes"}])
async def reap_stuck_jobs() -> None:
    """Return jobs abandoned by a dead worker to the uploaded state."""
    if not await _take_lock("reap_stuck_jobs"):
        return
    resources = await get_resources()
    async with resources.db.write_sessions() as session:
        ids = list(
            (
                await session.scalars(
                    select(Video.id).where(
                        Video.status == "processing",
                        Video.updated_at < func.now() - text("interval '20 minutes'"),
                        Video.removed_at.is_(None),
                    )
                )
            ).all()
        )
        if ids:
            await session.execute(
                update(Video)
                .where(Video.id.in_(ids))
                .values(status="uploaded", failure_reason=None)
            )
            await session.commit()

    if ids:
        await resources.redis.delete(*(f"transcode:lock:{video_id}" for video_id in ids))
        from app.worker.tasks.transcode import transcode_video

        for video_id in ids:
            await transcode_video.kiq(video_id)


@broker.task(schedule=[{"cron": "0 * * * *", "schedule_id": "cleanup_pending_uploads"}])
async def cleanup_pending_uploads() -> None:
    """Delete abandoned upload rows and abort their multipart uploads."""
    if not await _take_lock("cleanup_pending_uploads"):
        return
    resources = await get_resources()
    async with resources.db.write_sessions() as session:
        videos = list(
            (
                await session.scalars(
                    select(Video).where(
                        Video.status == "pending_upload",
                        Video.created_at < func.now() - text("interval '24 hours'"),
                        Video.multipart_upload_id.is_not(None),
                    )
                )
            ).all()
        )
        for video in videos:
            if video.multipart_upload_id:
                await resources.storage.abort_multipart_upload(
                    video.original_key,
                    video.multipart_upload_id,
                )
            await session.delete(video)
        if videos:
            await session.commit()
