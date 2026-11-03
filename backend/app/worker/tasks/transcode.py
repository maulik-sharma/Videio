"""The idempotent video transcode task."""

from __future__ import annotations

import secrets
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select

from app.db.models import TranscodeAttempt, Video
from app.services.storage import StorageError
from app.worker.broker import broker
from app.worker.ffmpeg import (
    PermanentTranscodeError,
    RetryableTranscodeError,
    probe_media,
    transcode_hls,
)
from app.worker.runtime import get_resources


def _content_type(path: Path) -> str:
    return {
        ".m3u8": "application/vnd.apple.mpegurl",
        ".ts": "video/mp2t",
        ".jpg": "image/jpeg",
    }.get(path.suffix, "application/octet-stream")


async def _finish_attempt(
    attempt_id: int,
    *,
    outcome: str,
    error: str | None = None,
) -> None:
    resources = await get_resources()
    async with resources.db.write_sessions() as session:
        attempt = await session.get(TranscodeAttempt, attempt_id)
        if attempt is not None:
            attempt.finished_at = func.now()
            attempt.outcome = outcome
            attempt.error = error
            await session.commit()


async def _mark_failed(video_id: int, attempt_id: int, reason: str) -> None:
    resources = await get_resources()
    async with resources.db.write_sessions() as session:
        video = await session.get(Video, video_id)
        if video is not None and video.removed_at is None:
            video.status = "failed"
            video.failure_reason = reason[:2000]
        attempt = await session.get(TranscodeAttempt, attempt_id)
        if attempt is not None:
            attempt.finished_at = func.now()
            attempt.outcome = "failed"
            attempt.error = reason[:2000]
        await session.commit()


async def _acquire_lock(video_id: int) -> tuple[str, str] | None:
    resources = await get_resources()
    token = secrets.token_urlsafe(18)
    key = f"transcode:lock:{video_id}"
    acquired = await resources.redis.set(key, token, nx=True, ex=30 * 60)
    return (key, token) if acquired else None


async def _release_lock(lock: tuple[str, str]) -> None:
    resources = await get_resources()
    key, token = lock
    await resources.redis.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end",
        1,
        key,
        token,
    )


@broker.task(retry_on_error=True, max_retries=3, delay=10)
async def transcode_video(video_id: int) -> None:
    """Transcode one uploaded original and publish deterministic HLS objects."""
    lock = await _acquire_lock(video_id)
    if lock is None:
        return

    resources = await get_resources()
    attempt_id: int | None = None
    try:
        async with resources.db.write_sessions() as session:
            video = await session.scalar(
                select(Video).where(Video.id == video_id).with_for_update()
            )
            if video is None or video.removed_at is not None or video.status == "ready":
                return
            if video.status == "pending_upload":
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

        with TemporaryDirectory(prefix=f"videio-{video_id}-") as temp_dir:
            workdir = Path(temp_dir) / "hls"
            source = Path(temp_dir) / "original"
            await resources.storage.download_original(original_key, source)
            probe = await probe_media(source)
            master, thumbnail, renditions = await transcode_hls(source, workdir, probe)

            media_prefix = f"videos/{public_id}"
            for output in workdir.rglob("*"):
                if output.is_file():
                    relative = output.relative_to(workdir).as_posix()
                    await resources.storage.upload_media_file(
                        output,
                        f"{media_prefix}/{relative}",
                        _content_type(output),
                    )

            rendition_data: dict[str, object] = {
                rendition.name: {
                    "playlist": f"{media_prefix}/{rendition.name}/index.m3u8",
                    "height": min(rendition.height, probe.height),
                    "bitrate_kbps": rendition.bitrate_kbps,
                }
                for rendition in renditions
            }
            rendition_data["master"] = f"{media_prefix}/{master.name}"
            thumbnail_key = f"{media_prefix}/{thumbnail.name}"

            async with resources.db.write_sessions() as session:
                video = await session.get(Video, video_id, with_for_update=True)
                if video is None or video.removed_at is not None:
                    return
                video.status = "ready"
                video.duration_s = probe.duration_s
                video.renditions = rendition_data
                video.thumbnail_key = thumbnail_key
                video.failure_reason = None
                await session.commit()
            await _finish_attempt(attempt_id, outcome="ready")
    except PermanentTranscodeError as exc:
        if attempt_id is not None:
            await _mark_failed(video_id, attempt_id, str(exc))
        raise
    except RetryableTranscodeError:
        if attempt_id is not None:
            await _finish_attempt(attempt_id, outcome="retry", error="temporary worker error")
        raise
    except StorageError as exc:
        if attempt_id is not None:
            await _finish_attempt(attempt_id, outcome="retry", error=str(exc))
        raise RetryableTranscodeError(str(exc)) from exc
    except Exception as exc:
        if attempt_id is not None:
            await _finish_attempt(attempt_id, outcome="retry", error=str(exc))
        raise RetryableTranscodeError("The transcode task failed temporarily.") from exc
    finally:
        await _release_lock(lock)
