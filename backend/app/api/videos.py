"""Video metadata, direct-to-object-storage uploads, owner edits and view counts."""

import logging

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import desc, literal, select, tuple_
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    BrokerDep,
    CurrentUser,
    OptionalActor,
    ReadSession,
    RedisDep,
    SettingsDep,
    StorageDep,
    WriteSession,
    client_ip,
)
from app.api.schemas import (
    CompleteVideoIn,
    VideoCreateIn,
    VideoPageOut,
    VideoSummaryOut,
    VideoUpdateIn,
    VideoUploadOut,
    expected_part_count,
)
from app.core.cursors import InvalidCursor, VideoCursor, decode_cursor, encode_cursor
from app.core.errors import AppError
from app.core.ids import new_public_id
from app.core.policy import can, is_owner
from app.db.models import User, Video
from app.services import cache, rate_limit
from app.services.cache import VideoRecord, video_ref_filter
from app.services.read_your_writes import mark_user_wrote
from app.services.storage import StorageError, StorageVerificationError
from app.worker.broker import VideioBroker
from app.worker.tasks.transcode import transcode_video

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["videos"])


def to_summary(record: VideoRecord, pending_views: int = 0) -> VideoSummaryOut:
    ready = record.status == "ready"
    return VideoSummaryOut(
        id=record.id,
        owner_id=record.owner_id,
        owner_name=record.owner_name,
        public_id=record.public_id,
        title=record.title,
        description=record.description,
        visibility=record.visibility,
        status=record.status,
        size_bytes=record.size_bytes,
        content_type=record.content_type,
        duration_s=record.duration_s,
        renditions=record.renditions,
        thumbnail_key=record.thumbnail_key,
        playback_url=f"/media/{record.public_id}/master.m3u8" if ready else None,
        thumbnail_url=(
            f"/media/{record.public_id}/thumbnail.jpg" if record.thumbnail_key is not None else None
        ),
        # Postgres has the flushed total; Redis holds views since the last flush.
        view_count=record.view_count + pending_views,
        failure_reason=record.failure_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
        removed_at=record.removed_at,
        removal_reason=record.removal_reason,
    )


async def summaries(redis: RedisDep, records: list[VideoRecord]) -> list[VideoSummaryOut]:
    pending = await cache.pending_views(redis, [record.id for record in records])
    return [to_summary(record, pending.get(record.id, 0)) for record in records]


def _not_found() -> AppError:
    return AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")


def _removed(record: VideoRecord | Video) -> AppError:
    return AppError(
        410,
        "VIDEO_REMOVED",
        "An administrator removed this video.",
        extra={"reason": record.removal_reason},
    )


def parse_cursor(cursor: str | None) -> VideoCursor | None:
    if not cursor:
        return None
    try:
        return decode_cursor(cursor)
    except InvalidCursor as exc:
        raise AppError(400, "INVALID_CURSOR", "That pagination cursor is invalid.") from exc


def _page(records: list[VideoRecord], limit: int) -> tuple[list[VideoRecord], str | None]:
    """Split `limit + 1` rows into one page and the cursor for the next, if any."""
    if len(records) <= limit:
        return records, None
    page = records[:limit]
    return page, encode_cursor(page[-1].created_at, page[-1].id)


def _storage_unavailable() -> AppError:
    return AppError(
        503,
        "STORAGE_UNAVAILABLE",
        "Object storage is temporarily unavailable. Try again shortly.",
    )


async def _enqueue_transcode(broker: VideioBroker, video_id: int) -> None:
    """Publish the transcode job. On failure the row stays `uploaded`, so the
    browser's retry of /complete publishes it again (the task is idempotent)."""
    unavailable = AppError(
        503, "QUEUE_UNAVAILABLE", "Video processing is temporarily unavailable. Try again."
    )
    if broker.write_channel is None or broker.write_channel.is_closed:
        raise unavailable
    try:
        await transcode_video.kiq(video_id)
    except Exception as exc:
        raise unavailable from exc


async def _owned_video_for_update(session: WriteSession, video_ref: str, user: User) -> Video:
    """Lock the row for a change by its owner, answering as the policy says otherwise.

    Someone who may not even see the video gets 404, so private videos do not
    leak their existence; someone who may see it but not change it gets 403.
    """
    video = await session.scalar(select(Video).where(video_ref_filter(video_ref)).with_for_update())
    if video is None:
        raise _not_found()
    if not can(user, "view", video) and not is_owner(user, video):
        raise _not_found()
    return video


@router.post("/videos", response_model=VideoUploadOut, status_code=status.HTTP_201_CREATED)
async def create_video_upload(
    body: VideoCreateIn,
    user: CurrentUser,
    session: WriteSession,
    redis: RedisDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> VideoUploadOut:
    """Create metadata and return presigned PUT URLs for every multipart part."""
    if body.size_bytes > settings.max_upload_size_bytes:
        raise AppError(413, "FILE_TOO_LARGE", "Videos must be 500 MB or smaller.")

    await rate_limit.enforce(
        redis,
        f"upload:user:{user.id}",
        rate_limit.Limit(settings.upload_limit_per_user, settings.upload_window_s),
    )

    part_count = expected_part_count(body.size_bytes, settings.s3_part_size_bytes)
    video = Video(
        public_id=new_public_id(),
        owner_id=user.id,
        title=body.title,
        description=body.description,
        visibility=body.visibility,
        status="pending_upload",
        original_size_bytes=body.size_bytes,
        original_content_type=body.content_type,
    )
    video.original_key = f"originals/{video.public_id}"
    session.add(video)
    upload_id: str | None = None
    try:
        await session.flush()
        upload_id = await storage.create_multipart_upload(video.original_key, body.content_type)
        urls = await storage.presign_upload_parts(video.original_key, upload_id, part_count)
        video.multipart_upload_id = upload_id
        await session.commit()
    except StorageError as exc:
        await session.rollback()
        if upload_id is not None:
            await storage.abort_multipart_upload(video.original_key, upload_id)
        raise _storage_unavailable() from exc
    except IntegrityError as exc:
        await session.rollback()
        if upload_id is not None:
            await storage.abort_multipart_upload(video.original_key, upload_id)
        raise AppError(503, "UPLOAD_COULD_NOT_START", "The upload could not be started.") from exc
    except Exception:
        await session.rollback()
        if upload_id is not None:
            await storage.abort_multipart_upload(video.original_key, upload_id)
        raise

    await mark_user_wrote(redis, user.id, settings.rw_flag_ttl_s)
    return VideoUploadOut(
        id=video.id,
        public_id=video.public_id,
        title=video.title,
        description=video.description,
        visibility=video.visibility,
        status=video.status,
        size_bytes=video.original_size_bytes,
        content_type=video.original_content_type,
        part_size_bytes=settings.s3_part_size_bytes,
        parts=[
            {"part_number": part_number, "url": url}
            for part_number, url in enumerate(urls, start=1)
        ],
    )


@router.post(
    "/videos/{video_ref}/complete",
    response_model=VideoSummaryOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_video_upload(
    video_ref: str,
    body: CompleteVideoIn,
    user: CurrentUser,
    session: WriteSession,
    redis: RedisDep,
    settings: SettingsDep,
    storage: StorageDep,
    broker: BrokerDep,
) -> VideoSummaryOut:
    """Complete the S3 multipart upload and make the video available to workers."""
    if not body.has_unique_part_numbers():
        raise AppError(400, "INVALID_UPLOAD_PARTS", "Each upload part number must be unique.")

    video = await session.scalar(
        select(Video).where(video_ref_filter(video_ref), Video.owner_id == user.id)
    )
    if video is None:
        raise _not_found()

    # Retrying the browser's final request is safe after the database update.
    if video.status == "uploaded":
        await _enqueue_transcode(broker, video.id)
        return to_summary(VideoRecord.from_row(video, user.display_name))
    if video.status != "pending_upload" or not video.multipart_upload_id:
        raise AppError(409, "UPLOAD_NOT_ACTIVE", "This upload is no longer active.")

    part_count = expected_part_count(video.original_size_bytes, settings.s3_part_size_bytes)
    part_numbers = sorted(part.part_number for part in body.parts)
    if part_numbers != list(range(1, part_count + 1)):
        raise AppError(
            400,
            "INVALID_UPLOAD_PARTS",
            f"Expected exactly {part_count} sequential upload parts.",
        )

    parts = [
        {"PartNumber": part.part_number, "ETag": part.etag}
        for part in sorted(body.parts, key=lambda part: part.part_number)
    ]
    try:
        await storage.complete_multipart_upload(
            video.original_key, video.multipart_upload_id, parts
        )
        object_size = await storage.object_size(video.original_key)
    except StorageVerificationError as exc:
        raise AppError(
            503,
            "STORAGE_UNAVAILABLE",
            "Object storage could not verify the upload. Try again shortly.",
        ) from exc
    except StorageError as exc:
        raise AppError(
            400,
            "UPLOAD_COMPLETION_FAILED",
            "Object storage could not complete these parts. Check the ETags and try again.",
        ) from exc
    if object_size != video.original_size_bytes:
        await storage.delete_object(video.original_key)
        raise AppError(
            400,
            "UPLOAD_SIZE_MISMATCH",
            "The uploaded object size does not match the size declared at upload start.",
        )

    video.status = "uploaded"
    video.multipart_upload_id = None
    await session.commit()
    # SQLAlchemy expires expression-based onupdate columns after this UPDATE.
    # Refresh explicitly while still inside the async endpoint instead of
    # letting response serialization trigger an implicit MissingGreenlet load.
    await session.refresh(video)
    await mark_user_wrote(redis, user.id, settings.rw_flag_ttl_s)
    # Not in the feed yet, but the owner may have the pending row cached.
    await cache.publish_video_change(redis, session, video.public_id, feed=False)
    await _enqueue_transcode(broker, video.id)
    return to_summary(VideoRecord.from_row(video, user.display_name))


@router.get("/me/videos", response_model=VideoPageOut)
async def my_videos(
    user: CurrentUser,
    session: ReadSession,
    redis: RedisDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> VideoPageOut:
    """List the user's videos with keyset pagination, newest first.

    Includes videos an admin took down, with the reason, so the owner can see why.
    """
    after = parse_cursor(cursor)
    statement = (
        select(Video, User.display_name)
        .join(User, User.id == Video.owner_id)
        .where(Video.owner_id == user.id)
    )
    if after is not None:
        statement = statement.where(
            tuple_(Video.created_at, Video.id)
            < tuple_(literal(after.created_at), literal(after.video_id))
        )
    statement = statement.order_by(desc(Video.created_at), desc(Video.id)).limit(limit + 1)
    rows = (await session.execute(statement)).all()
    records, next_cursor = _page(
        [VideoRecord.from_row(video, owner_name) for video, owner_name in rows], limit
    )
    return VideoPageOut(items=await summaries(redis, records), next_cursor=next_cursor)


@router.get("/feed", response_model=VideoPageOut)
async def public_feed(
    response: Response,
    session: ReadSession,
    redis: RedisDep,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> VideoPageOut:
    """Public ready videos, newest first. The first page is cached for 30 seconds;
    deeper pages read the replica with a keyset cursor, never OFFSET."""
    after = parse_cursor(cursor)
    if after is None:
        head, hit = await cache.get_feed_head(redis, session)
        response.headers["X-Cache"] = "HIT" if hit else "MISS"
        records, next_cursor = _page(head, limit)
    else:
        response.headers["X-Cache"] = "BYPASS"
        records, next_cursor = _page(await cache.load_feed(session, after, limit + 1), limit)
    return VideoPageOut(items=await summaries(redis, records), next_cursor=next_cursor)


@router.get(
    "/videos/{video_ref}",
    response_model=VideoSummaryOut,
    responses={410: {"description": "The owner's video was taken down by an admin"}},
)
async def get_video(
    video_ref: str,
    response: Response,
    session: ReadSession,
    redis: RedisDep,
    actor: OptionalActor,
) -> VideoSummaryOut:
    """Metadata and status. Cached for five minutes, checked against the policy per caller."""
    record, hit = await cache.get_video_record(redis, session, video_ref)
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    if record is None:
        raise _not_found()
    if not can(actor, "view", record):
        if record.removed_at is not None and is_owner(actor, record):
            raise _removed(record)
        raise _not_found()
    pending = await cache.pending_views(redis, [record.id])
    return to_summary(record, pending.get(record.id, 0))


@router.patch("/videos/{video_ref}", response_model=VideoSummaryOut)
async def update_video(
    video_ref: str,
    body: VideoUpdateIn,
    user: CurrentUser,
    session: WriteSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> VideoSummaryOut:
    """Change the title, description or visibility. Owner only."""
    video = await _owned_video_for_update(session, video_ref, user)
    if not can(user, "edit", video):
        if video.removed_at is not None and is_owner(user, video):
            raise _removed(video)
        raise AppError(403, "FORBIDDEN", "Only the uploader can edit this video.")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(video, field, value)
    await session.commit()
    await session.refresh(video)
    await mark_user_wrote(redis, user.id, settings.rw_flag_ttl_s)
    await cache.publish_video_change(redis, session, video.public_id)
    return to_summary(VideoRecord.from_row(video, user.display_name))


@router.delete("/videos/{video_ref}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video(
    video_ref: str,
    user: CurrentUser,
    session: WriteSession,
    redis: RedisDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> None:
    """Delete the video, its original and every generated file. Owner only.

    Objects go first: if storage is down the row stays and the owner can retry,
    rather than leaving files nothing points at. A transcode that finishes
    after this finds no row and removes what it uploaded (see the worker).
    """
    video = await _owned_video_for_update(session, video_ref, user)
    if not can(user, "delete", video):
        raise AppError(403, "FORBIDDEN", "Only the uploader can delete this video.")

    public_id = video.public_id
    try:
        if video.multipart_upload_id:
            await storage.abort_multipart_upload(video.original_key, video.multipart_upload_id)
        await storage.delete_upload(video.original_key)
        await storage.delete_media_prefix(f"videos/{public_id}/")
    except StorageError as exc:
        await session.rollback()
        raise _storage_unavailable() from exc

    await session.delete(video)
    await session.commit()
    await mark_user_wrote(redis, user.id, settings.rw_flag_ttl_s)
    await cache.publish_video_change(redis, session, public_id)
    logger.info("Video %s deleted by its owner", public_id)


@router.post("/videos/{video_ref}/view")
async def count_video_view(
    video_ref: str,
    session: ReadSession,
    redis: RedisDep,
    actor: OptionalActor,
    ip: str = Depends(client_ip),
) -> dict[str, bool]:
    """Count at most one view per user or IP for a video during one hour.

    The count goes to Redis only; a scheduled task adds it to Postgres every minute.
    """
    record, _ = await cache.get_video_record(redis, session, video_ref)
    if (
        record is None
        or record.status != "ready"
        or record.removed_at is not None  # admins reviewing a takedown are not viewers
        or not can(actor, "view", record)
    ):
        raise _not_found()
    viewer = str(actor.id) if actor is not None else ip
    if not await redis.set(f"viewed:{record.id}:{viewer}", "1", nx=True, ex=60 * 60):
        return {"counted": False}
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(f"views:{record.id}")
        pipe.sadd("views:dirty", record.id)
        await pipe.execute()
    return {"counted": True}
