"""Video metadata and direct-to-object-storage upload endpoints."""

from datetime import datetime, timezone

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import desc, func, or_, select, tuple_
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    AccessUserId,
    BrokerDep,
    CurrentUser,
    CurrentAdmin,
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
    VideoUploadOut,
    expected_part_count,
)
from app.core.cursors import InvalidCursor, decode_cursor, encode_cursor
from app.core.errors import AppError
from app.core.policy import can
from app.core.security import decode_access_token
from app.db.models import User, Video
from app.services import rate_limit
from app.services.audit import log_action
from app.services.cache import cache_service, set_cache_service
from app.services.read_your_writes import mark_user_wrote
from app.services.storage import StorageError, StorageVerificationError
from app.worker.broker import VideioBroker
from app.worker.tasks.transcode import transcode_video


router = APIRouter(prefix="/api", tags=["videos"])


def _video_ref_filter(video_ref: str) -> Any:
    """Accept public ids in URLs while tolerating internal numeric ids for APIs."""
    if video_ref.isdecimal():
        return or_(Video.id == int(video_ref), Video.public_id == video_ref)
    return Video.public_id == video_ref


def _summary(video: Video, owner_name: str) -> VideoSummaryOut:
    return VideoSummaryOut(
        id=video.id,
        owner_name=owner_name,
        public_id=video.public_id,
        title=video.title,
        description=video.description,
        visibility=video.visibility,
        status=video.status,
        size_bytes=video.original_size_bytes,
        content_type=video.original_content_type,
        duration_s=video.duration_s,
        renditions=video.renditions,
        thumbnail_key=video.thumbnail_key,
        playback_url=(f"/media/{video.public_id}/master.m3u8" if video.status == "ready" else None),
        thumbnail_url=(
            f"/media/{video.public_id}/thumbnail.jpg" if video.thumbnail_key is not None else None
        ),
        view_count=video.view_count,
        failure_reason=video.failure_reason,
        created_at=video.created_at,
        updated_at=video.updated_at,
    )


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
        select(Video).where(_video_ref_filter(video_ref), Video.owner_id == user.id)
    )
    if video is None:
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")

    # Retrying the browser's final request is safe after the database update.
    if video.status == "uploaded":
        await _enqueue_transcode(broker, video.id)
        return _summary(video, user.display_name)
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
    await _enqueue_transcode(broker, video.id)
    return _summary(video, user.display_name)


@router.get("/me/videos", response_model=VideoPageOut)
async def my_videos(
    user: CurrentUser,
    session: ReadSession,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> VideoPageOut:
    """List the user's videos with keyset pagination, newest first."""
    decoded = None
    if cursor:
        try:
            decoded = decode_cursor(cursor)
        except InvalidCursor as exc:
            raise AppError(400, "INVALID_CURSOR", "That pagination cursor is invalid.") from exc

    statement = select(Video, User.display_name).join(User, User.id == Video.owner_id)
    statement = statement.where(Video.owner_id == user.id)
    if decoded is not None:
        statement = statement.where(
            tuple_(Video.created_at, Video.id) < tuple_(decoded.created_at, decoded.video_id)
        )
    statement = statement.order_by(desc(Video.created_at), desc(Video.id)).limit(limit + 1)
    rows = list((await session.execute(statement)).tuples().all())
    next_cursor = None
    if len(rows) > limit:
        rows.pop()
        last = rows[-1][0]
        next_cursor = encode_cursor(last.created_at, last.id)
    return VideoPageOut(
        items=[_summary(video, owner_name) for video, owner_name in rows],
        next_cursor=next_cursor,
    )


@router.get("/feed", response_model=VideoPageOut)
async def public_feed(
    session: ReadSession,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> VideoPageOut:
    """Return public ready videos from the replica using a keyset cursor."""
    # Try cache first
    cached_items, cached_cursor = await cache_service.get_cached_feed_page(1)
    if cached_items is not None and cursor is None:
        return VideoPageOut(
            items=cached_items[:limit],
            next_cursor=cached_cursor,
        )

    decoded = None
    if cursor:
        try:
            decoded = decode_cursor(cursor)
        except InvalidCursor as exc:
            raise AppError(400, "INVALID_CURSOR", "That pagination cursor is invalid.") from exc

    statement = (
        select(Video, User.display_name)
        .join(User, User.id == Video.owner_id)
        .where(
            Video.visibility == "public",
            Video.status == "ready",
            Video.removed_at.is_(None),
        )
    )
    if decoded is not None:
        statement = statement.where(
            tuple_(Video.created_at, Video.id) < tuple_(decoded.created_at, decoded.video_id)
        )
    statement = statement.order_by(desc(Video.created_at), desc(Video.id)).limit(limit + 1)
    rows = list((await session.execute(statement)).tuples().all())
    next_cursor = None
    if len(rows) > limit:
        rows.pop()
        last = rows[-1][0]
        next_cursor = encode_cursor(last.created_at, last.id)

    items = [_summary(video, owner_name) for video, owner_name in rows]
    
    # Cache the first page
    if len(items) > 0 and cursor is None:
        await cache_service.cache_feed_page(1, items, next_cursor)
    
    return VideoPageOut(
        items=items,
        next_cursor=next_cursor,
    )


@router.get("/videos/{video_ref}", response_model=VideoSummaryOut)
async def get_video(video_ref: str, session: ReadSession, user_id: AccessUserId) -> VideoSummaryOut:
    """Return metadata for a public/unlisted video or its owner's private video."""
    # Try cache first
    if user_id is not None:
        cached = await cache_service.get_cached_video_meta(int(video_ref) if video_ref.isdecimal() else None)
        if cached is not None:
            # Reconstruct Video object from cache or use cached data
            # For now, fall through to DB
            pass
    
    row = (
        await session.execute(
            select(Video, User.display_name)
            .join(User, User.id == Video.owner_id)
            .where(_video_ref_filter(video_ref))
        )
    ).first()
    video, owner_name = row.tuple() if row is not None else (None, "")
    if (
        video is None
        or video.removed_at is not None
        or (video.visibility == "private" and video.owner_id != user_id)
    ):
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")
    if video.status != "ready" and video.owner_id != user_id:
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")
    
    # Cache the metadata
    summary_data = {
        "id": video.id,
        "public_id": video.public_id,
        "owner_name": owner_name,
        "title": video.title,
        "description": video.description,
        "visibility": video.visibility,
        "status": video.status,
        "size_bytes": video.original_size_bytes,
        "content_type": video.original_content_type,
        "duration_s": video.duration_s,
        "renditions": video.renditions,
        "thumbnail_key": video.thumbnail_key,
        "playback_url": (f"/media/{video.public_id}/master.m3u8" if video.status == "ready" else None),
        "thumbnail_url": (
            f"/media/{video.public_id}/thumbnail.jpg" if video.thumbnail_key is not None else None
        ),
        "view_count": video.view_count,
        "failure_reason": video.failure_reason,
        "created_at": video.created_at,
        "updated_at": video.updated_at,
    }
    await cache_service.cache_video_meta(video.id, summary_data)
    
    return _summary(video, owner_name)


@router.post("/videos/{video_ref}/view")
async def count_video_view(
    video_ref: str,
    session: ReadSession,
    redis: RedisDep,
    user_id: AccessUserId,
    ip: str = Depends(client_ip),
) -> dict[str, bool]:
    """Count at most one view per user or IP for a video during one hour."""
    video = await session.scalar(select(Video).where(_video_ref_filter(video_ref)))
    if (
        video is None
        or video.removed_at is not None
        or video.status != "ready"
        or (video.visibility == "private" and video.owner_id != user_id)
    ):
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")
    viewer = str(user_id) if user_id is not None else ip
    marker = f"viewed:{video.id}:{viewer}"
    if not await redis.set(marker, "1", nx=True, ex=60 * 60):
        return {"counted": False}
    await redis.incr(f"views:{video.id}")
    await redis.sadd("views:dirty", video.id)
    return {"counted": True}


@router.get("/internal/authz", include_in_schema=False)
async def internal_authz(
    video_ref: str,
    session: ReadSession,
    settings: SettingsDep,
    request: Request,
    user: CurrentUser,
) -> tuple[int, str]:
    """nginx auth_request endpoint: 204 if authorized, 403 otherwise.

    Called by nginx internal subrequest. The user's access cookie is attached
    by the browser (SameSite=Lax) and decoded by the existing CurrentUser dep.
    """
    from app.db.models import User as _User

    video = await session.scalar(
        select(Video).where(_video_ref_filter(video_ref))
    )
    if video is None:
        return 404, "VIDEO_NOT_FOUND"

    if user is None:
        # Anonymous viewer: only public/ready videos
        if (
            video.visibility in ("public", "unlisted")
            and video.removed_at is None
            and video.status == "ready"
        ):
            return 204, ""
        return 403, "FORBIDDEN"

    # Owner or admin
    if video.owner_id == user.id or user.role == "admin":
        if video.removed_at is not None:
            return 200, "REMOVED"  # owner/admin can see removed videos
        return 204, ""

    # Logged-in but not owner
    if video.visibility == "private" and video.removed_at is None:
        return 403, "FORBIDDEN"
    if video.visibility in ("public", "unlisted") and video.removed_at is None and video.status == "ready":
        return 204, ""
    return 403, "FORBIDDEN"


@router.patch("/videos/{video_ref}", response_model=VideoSummaryOut)
async def update_video(
    video_ref: str,
    body: dict[str, Any],
    user: CurrentUser,
    session: WriteSession,
) -> VideoSummaryOut:
    """Edit title, description, or visibility. Owner only."""
    video = await session.scalar(
        select(Video).where(_video_ref_filter(video_ref), Video.owner_id == user.id)
    )
    if video is None:
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")

    if "title" in body:
        video.title = body["title"]
    if "description" in body:
        video.description = body["description"]
    if "visibility" in body:
        if body["visibility"] not in ("public", "unlisted", "private"):
            raise AppError(400, "INVALID_VISIBILITY", "Visibility must be public, unlisted, or private.")
        video.visibility = body["visibility"]

    await session.commit()
    await session.refresh(video)
    # Invalidate cache on write
    if user_id is not None:
        await cache_service.invalidate_video_meta(video.id)
        # Invalidate feed cache for first page
        await cache_service.invalidate_feed_page(1)
    return _summary(video, user.display_name)


@router.delete("/videos/{video_ref}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video(
    video_ref: str,
    user: CurrentUser,
    session: WriteSession,
    storage: StorageDep,
) -> None:
    """Permanently delete a video and its objects. Owner only."""
    video = await session.scalar(
        select(Video).where(_video_ref_filter(video_ref), Video.owner_id == user.id)
    )
    if video is None:
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")

    # Delete objects from storage
    if video.original_key:
        await storage.delete_object(video.original_key)
    if video.thumbnail_key:
        await storage.delete_object(video.thumbnail_key)
    for rendition in video.renditions or {}:
        key = rendition.get("key") or f"videos/{video.public_id}/{rendition.get('name')}"
        await storage.delete_object(key)

    video.removed_at = func.now()
    video.removed_by = user.id
    video.removal_reason = "deleted by owner"
    # Invalidate cache on write
    await cache_service.invalidate_video_meta(video.id)
    await cache_service.invalidate_feed_page(1)
    await session.commit()
    await session.refresh(video)
