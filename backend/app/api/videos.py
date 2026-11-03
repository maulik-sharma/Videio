"""Video metadata and direct-to-object-storage upload endpoints."""

from typing import Any

from fastapi import APIRouter, Query, status
from sqlalchemy import desc, or_, select, tuple_
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    CurrentUser,
    ReadSession,
    RedisDep,
    SettingsDep,
    StorageDep,
    WriteSession,
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
from app.core.ids import new_public_id
from app.db.models import Video
from app.services import rate_limit
from app.services.read_your_writes import mark_user_wrote
from app.services.storage import StorageError, StorageVerificationError

router = APIRouter(prefix="/api", tags=["videos"])


def _video_ref_filter(video_ref: str) -> Any:
    """Accept public ids in URLs while tolerating internal numeric ids for APIs."""
    if video_ref.isdecimal():
        return or_(Video.id == int(video_ref), Video.public_id == video_ref)
    return Video.public_id == video_ref


def _summary(video: Video) -> VideoSummaryOut:
    return VideoSummaryOut(
        id=video.id,
        public_id=video.public_id,
        title=video.title,
        description=video.description,
        visibility=video.visibility,
        status=video.status,
        size_bytes=video.original_size_bytes,
        content_type=video.original_content_type,
        duration_s=video.duration_s,
        thumbnail_key=video.thumbnail_key,
        view_count=video.view_count,
        created_at=video.created_at,
        updated_at=video.updated_at,
    )


def _storage_unavailable() -> AppError:
    return AppError(
        503,
        "STORAGE_UNAVAILABLE",
        "Object storage is temporarily unavailable. Try again shortly.",
    )


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
        return _summary(video)
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
    await mark_user_wrote(redis, user.id, settings.rw_flag_ttl_s)
    return _summary(video)


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

    statement = select(Video).where(Video.owner_id == user.id)
    if decoded is not None:
        statement = statement.where(
            tuple_(Video.created_at, Video.id) < tuple_(decoded.created_at, decoded.video_id)
        )
    statement = statement.order_by(desc(Video.created_at), desc(Video.id)).limit(limit + 1)
    videos = list((await session.scalars(statement)).all())
    next_cursor = None
    if len(videos) > limit:
        videos.pop()
        last = videos[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    return VideoPageOut(items=[_summary(video) for video in videos], next_cursor=next_cursor)
