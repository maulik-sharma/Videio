"""The idempotent video transcode task."""

from __future__ import annotations

import socket
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select
from taskiq import TaskiqMessage

from app.db.models import TranscodeAttempt, Video
from app.services import cache
from app.services.storage import StorageError
from app.worker.broker import MAX_ATTEMPTS, broker
from app.worker.ffmpeg import (
    PermanentTranscodeError,
    RetryableTranscodeError,
    probe_media,
    transcode_hls,
)
from app.worker.locks import redis_lock
from app.worker.runtime import get_resources

# The worker extends this lock every minute while it transcodes, so a live job
# keeps it however long it runs, and a dead worker's lock lapses within 3 min.
LOCK_TTL_MS = 3 * 60 * 1000
# A copy of the job that finds the lock taken checks back this much later.
BUSY_RECHECK_S = 60

EXHAUSTED_REASON = "Processing failed several times. Try uploading the video again."


def _content_type(path: Path) -> str:
    return {
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/mp2t",
        ".jpg": "image/jpeg",
    }.get(path.suffix, "application/octet-stream")


async def _finish_attempt(attempt_id: int, *, outcome: str, error: str | None = None) -> None:
    resources = await get_resources()
    async with resources.db.write_sessions() as session:
        attempt = await session.get(TranscodeAttempt, attempt_id)
        if attempt is not None:
            attempt.finished_at = func.now()
            attempt.outcome = outcome
            attempt.error = error[:2000] if error else None
            await session.commit()


async def _mark_failed(video_id: int, attempt_id: int, exc: PermanentTranscodeError) -> None:
    """Fail the video with the user-facing reason; keep ffmpeg's detail on the attempt."""
    resources = await get_resources()
    async with resources.db.write_sessions() as session:
        video = await session.get(Video, video_id)
        if video is not None and video.removed_at is None:
            video.status = "failed"
            video.failure_reason = str(exc)[:2000]
        attempt = await session.get(TranscodeAttempt, attempt_id)
        if attempt is not None:
            attempt.finished_at = func.now()
            attempt.outcome = "failed"
            attempt.error = exc.detail[:2000]
        await session.commit()
        if video is not None:
            await cache.publish_video_change(resources.redis, session, video.public_id, feed=False)


async def mark_exhausted(message: TaskiqMessage, exception: BaseException) -> None:
    """Final-failure hook: the last retry failed, so stop showing "processing".

    Permanent errors have already marked the video failed with a specific
    reason; this only settles videos still waiting on a retry.
    """
    del exception
    video_id = message.args[0] if message.args else message.kwargs.get("video_id")
    if video_id is None:
        return
    resources = await get_resources()
    async with resources.db.write_sessions() as session:
        video = await session.get(Video, int(video_id))
        if (
            video is not None
            and video.removed_at is None
            and video.status in {"uploaded", "processing"}
        ):
            video.status = "failed"
            video.failure_reason = EXHAUSTED_REASON
            await session.commit()
            await cache.publish_video_change(resources.redis, session, video.public_id, feed=False)


@broker.task(retry_on_error=True, max_retries=MAX_ATTEMPTS, dead_letter=True)
async def transcode_video(video_id: int) -> None:
    """Transcode one uploaded original and publish deterministic HLS objects."""
    resources = await get_resources()
    lock = redis_lock(resources.redis, f"transcode:lock:{video_id}", LOCK_TTL_MS, heartbeat=True)
    async with lock as held:
        if not held:
            # Someone holds the lock: either a live worker (then the re-check
            # finds the video done and stops) or a worker that was just killed
            # (then its lock lapses and the re-check picks the job up). Either
            # way, check back shortly instead of dropping the job. This does not
            # use up the retry budget.
            await transcode_video.kicker().with_labels(delay=BUSY_RECHECK_S).kiq(video_id)
            return
        await _transcode(video_id)


async def _transcode(video_id: int) -> None:
    resources = await get_resources()
    attempt_id: int | None = None
    try:
        async with resources.db.write_sessions() as session:
            video = await session.scalar(
                select(Video).where(Video.id == video_id).with_for_update()
            )
            # Idempotency: only uploaded videos, or ones a dead worker left
            # mid-transcode, need work. Ready, failed, removed or not-yet-uploaded
            # videos are left alone (re-run a failed one with `app.cli retranscode`).
            if video is None or video.removed_at is not None:
                return
            if video.status not in {"uploaded", "processing"}:
                return

            last_attempt = await session.scalar(
                select(func.max(TranscodeAttempt.attempt)).where(
                    TranscodeAttempt.video_id == video_id
                )
            )
            attempt = TranscodeAttempt(
                video_id=video_id,
                attempt=(last_attempt or 0) + 1,
                worker_id=socket.gethostname(),
            )
            video.status = "processing"
            video.failure_reason = None
            session.add(attempt)
            await session.commit()
            attempt_id = attempt.id
            original_key = video.original_key
            public_id = video.public_id
            await cache.publish_video_change(resources.redis, session, public_id, feed=False)

        with TemporaryDirectory(prefix=f"videio-{video_id}-") as temp_dir:
            workdir = Path(temp_dir) / "hls"
            source = Path(temp_dir) / "original"
            await resources.storage.download_original(original_key, source)
            probe = await probe_media(source)
            master, thumbnail, renditions = await transcode_hls(source, workdir, probe)

            # Deterministic keys: a retry overwrites the same objects.
            media_prefix = f"videos/{public_id}"
            for output in sorted(workdir.rglob("*")):
                # Upload master.m3u8 last, so it never points at missing playlists.
                if output.is_file() and output != master:
                    await resources.storage.upload_media_file(
                        output,
                        f"{media_prefix}/{output.relative_to(workdir).as_posix()}",
                        _content_type(output),
                    )
            await resources.storage.upload_media_file(
                master, f"{media_prefix}/{master.name}", _content_type(master)
            )

            rendition_data: dict[str, object] = {
                "master": f"{media_prefix}/{master.name}",
                "source": {"width": probe.width, "height": probe.height},
                "ladder": [
                    {
                        "name": rendition.name,
                        "width": rendition.width,
                        "height": rendition.height,
                        "bitrate_kbps": rendition.bitrate_kbps,
                        "playlist": f"{media_prefix}/{rendition.name}/index.m3u8",
                    }
                    for rendition in renditions
                ],
            }

            async with resources.db.write_sessions() as session:
                video = await session.get(Video, video_id, with_for_update=True)
                if video is None:
                    # The owner deleted the video while it was transcoding. Their
                    # delete already ran, so remove what this job just uploaded.
                    await resources.storage.delete_media_prefix(f"{media_prefix}/")
                    await _finish_attempt(attempt_id, outcome="deleted")
                    return
                # A takedown during the transcode still records the result; the
                # video stays hidden until an admin restores it.
                video.status = "ready"
                video.duration_s = probe.duration_s
                video.renditions = rendition_data
                video.thumbnail_key = f"{media_prefix}/{thumbnail.name}"
                video.failure_reason = None
                await session.commit()
                await cache.publish_video_change(resources.redis, session, public_id)
            await _finish_attempt(attempt_id, outcome="ready")
    except PermanentTranscodeError as exc:
        if attempt_id is not None:
            await _mark_failed(video_id, attempt_id, exc)
        raise
    except RetryableTranscodeError as exc:
        if attempt_id is not None:
            await _finish_attempt(attempt_id, outcome="retry", error=str(exc))
        raise
    except StorageError as exc:
        if attempt_id is not None:
            await _finish_attempt(attempt_id, outcome="retry", error=str(exc))
        raise RetryableTranscodeError(str(exc)) from exc
    except Exception as exc:
        if attempt_id is not None:
            await _finish_attempt(attempt_id, outcome="retry", error=f"{type(exc).__name__}: {exc}")
        raise RetryableTranscodeError("The transcode task failed temporarily.") from exc


broker.on_final_failure(transcode_video.task_name, mark_exhausted)
