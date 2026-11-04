"""Scheduled maintenance tasks. The scheduler only enqueues these functions."""

from __future__ import annotations

from sqlalchemy import case, func, select, text, update

from app.db.models import TranscodeAttempt, Video
from app.services import cache
from app.worker.broker import MAX_ATTEMPTS, broker
from app.worker.locks import redis_lock
from app.worker.runtime import get_resources

# Each periodic task holds this lock while it runs, so if a run overlaps the
# next one (slow database, or two copies of the message) the second does nothing.
TASK_LOCK_TTL_MS = 60_000


@broker.task(schedule=[{"cron": "* * * * *", "schedule_id": "flush_view_counters"}])
async def flush_view_counters() -> None:
    """Move Redis view increments to Postgres in one update per batch."""
    resources = await get_resources()
    async with redis_lock(resources.redis, "lock:flush_view_counters", TASK_LOCK_TTL_MS) as held:
        if not held:
            return
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
            flushed = await session.scalars(
                update(Video)
                .where(Video.id.in_(counts))
                .values(view_count=Video.view_count + case(counts, value=Video.id, else_=0))
                .returning(Video.public_id)
            )
            public_ids = list(flushed.all())
            await session.commit()
        # Cached records still carry the old total, and the Redis share of the
        # count just moved to Postgres, so drop them rather than undercount.
        await cache.forget_videos(resources.redis, public_ids)


@broker.task(schedule=[{"cron": "*/5 * * * *", "schedule_id": "reap_stuck_transcodes"}])
async def reap_stuck_jobs() -> None:
    """Re-kick transcodes abandoned by a worker that died mid-job.

    A video counts as abandoned when it has been `processing` for 20 minutes
    and nobody holds its transcode lock. A live worker holds the lock for the
    whole job, so a long transcode is never started twice; a dead worker's lock
    expires on its own. Videos that keep killing workers are failed after
    MAX_ATTEMPTS attempts instead of being retried forever.
    """
    from app.worker.tasks.transcode import EXHAUSTED_REASON, transcode_video

    resources = await get_resources()
    async with redis_lock(resources.redis, "lock:reap_stuck_jobs", TASK_LOCK_TTL_MS) as held:
        if not held:
            return
        async with resources.db.write_sessions() as session:
            rows = (
                await session.execute(
                    select(Video.id, Video.public_id, func.count(TranscodeAttempt.id))
                    .outerjoin(TranscodeAttempt, TranscodeAttempt.video_id == Video.id)
                    .where(
                        Video.status == "processing",
                        Video.updated_at < func.now() - text("interval '20 minutes'"),
                        Video.removed_at.is_(None),
                    )
                    .group_by(Video.id)
                )
            ).all()
            retry_ids: list[int] = []
            give_up_ids: list[int] = []
            changed: list[str] = []
            for video_id, public_id, attempts in rows:
                if await resources.redis.exists(f"transcode:lock:{video_id}"):
                    continue  # still being worked on
                (give_up_ids if attempts >= MAX_ATTEMPTS else retry_ids).append(video_id)
                changed.append(public_id)

            if give_up_ids:
                await session.execute(
                    update(Video)
                    .where(Video.id.in_(give_up_ids))
                    .values(status="failed", failure_reason=EXHAUSTED_REASON)
                )
            if retry_ids:
                await session.execute(
                    update(Video)
                    .where(Video.id.in_(retry_ids))
                    .values(status="uploaded", failure_reason=None)
                )
            await session.commit()
        await cache.forget_videos(resources.redis, changed)

        for video_id in retry_ids:
            await transcode_video.kiq(video_id)


@broker.task(schedule=[{"cron": "0 * * * *", "schedule_id": "cleanup_pending_uploads"}])
async def cleanup_pending_uploads() -> None:
    """Delete abandoned upload rows and abort their multipart uploads."""
    resources = await get_resources()
    async with redis_lock(
        resources.redis, "lock:cleanup_pending_uploads", TASK_LOCK_TTL_MS
    ) as held:
        if not held:
            return
        async with resources.db.write_sessions() as session:
            videos = list(
                (
                    await session.scalars(
                        select(Video).where(
                            Video.status == "pending_upload",
                            Video.created_at < func.now() - text("interval '24 hours'"),
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
        await cache.forget_videos(resources.redis, [video.public_id for video in videos])
