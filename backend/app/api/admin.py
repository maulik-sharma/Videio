"""Admin routes: take videos down and back, disable accounts, and the System panel.

Every change here writes an `audit_log` row in the same transaction as the
change itself, so there is never an action without a record of who and why.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, literal, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbDep, ReadSession, RedisDep, SettingsDep, WriteSession
from app.api.schemas import (
    AdminUserOut,
    AdminVideoOut,
    AdminVideoPageOut,
    AuditEntryOut,
    ReasonIn,
)
from app.api.videos import parse_cursor, to_summary
from app.core.cursors import encode_cursor
from app.core.errors import AppError
from app.core.policy import can
from app.db.models import AuditLog, User, Video
from app.services import cache
from app.services.cache import VideoRecord, video_ref_filter
from app.services.queues import QueueStats, queue_stats
from app.services.read_your_writes import mark_user_wrote
from app.services.sessions import RefreshTokenStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ReplicaStatus(BaseModel):
    name: str
    state: str
    sync_state: str
    # Bytes of WAL the primary has produced that the replica has not replayed yet.
    # Always available; 0 means fully caught up.
    lag_bytes: int
    # Postgres' measured replay delay. Null when the replica is idle and caught up.
    replay_lag_seconds: float | None


class ReplicationStatus(BaseModel):
    replicas: list[ReplicaStatus]
    # What the read engine is actually connected to: true means a hot standby.
    read_db_in_recovery: bool


class QueuesStatus(BaseModel):
    queues: list[QueueStats]
    # Set instead of `queues` being complete when the management API did not answer.
    error: str | None = None


class CacheStatus(BaseModel):
    hits: int
    misses: int
    hit_ratio: float | None


class SystemStatus(BaseModel):
    replication: ReplicationStatus
    queues: QueuesStatus
    videos_by_status: dict[str, int]
    removed_videos: int
    cache: dict[str, CacheStatus]
    # Videos with views counted in Redis that the next flush will write to Postgres.
    views_pending_flush: int


async def _replication(db: DbDep) -> ReplicationStatus:
    async with db.write_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT application_name,
                           state,
                           sync_state,
                           pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::bigint AS lag_bytes,
                           EXTRACT(EPOCH FROM replay_lag)::float AS replay_lag_seconds
                    FROM pg_stat_replication
                    ORDER BY application_name
                    """
                )
            )
        ).all()
    async with db.read_engine.connect() as conn:
        in_recovery = bool(await conn.scalar(text("SELECT pg_is_in_recovery()")))
    return ReplicationStatus(
        replicas=[
            ReplicaStatus(
                name=row.application_name,
                state=row.state,
                sync_state=row.sync_state,
                lag_bytes=row.lag_bytes or 0,
                replay_lag_seconds=row.replay_lag_seconds,
            )
            for row in rows
        ],
        read_db_in_recovery=in_recovery,
    )


@router.get("/system")
async def system(
    _: CurrentAdmin, db: DbDep, session: ReadSession, redis: RedisDep, settings: SettingsDep
) -> SystemStatus:
    """Live numbers for the admin System panel, which doubles as the demo dashboard."""
    try:
        queues = QueuesStatus(queues=await queue_stats(settings))
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("RabbitMQ management API did not answer: %s", exc)
        queues = QueuesStatus(queues=[], error="RabbitMQ's management API did not answer.")

    by_status = {
        status: count
        for status, count in (
            await session.execute(
                select(Video.status, func.count())
                .where(Video.removed_at.is_(None))
                .group_by(Video.status)
            )
        ).all()
    }
    removed = await session.scalar(
        select(func.count()).select_from(Video).where(Video.removed_at.is_not(None))
    )
    return SystemStatus(
        replication=await _replication(db),
        queues=queues,
        videos_by_status={
            status: by_status.get(status, 0)
            for status in ("pending_upload", "uploaded", "processing", "ready", "failed")
        },
        removed_videos=removed or 0,
        cache={
            name: CacheStatus.model_validate(values)
            for name, values in (await cache.stats(redis)).items()
        },
        views_pending_flush=await redis.scard("views:dirty"),
    )


def _admin_video(record: VideoRecord, owner: User) -> AdminVideoOut:
    return AdminVideoOut(
        **to_summary(record).model_dump(),
        owner_email=owner.email,
        owner_disabled=owner.disabled_at is not None,
    )


@router.get("/videos", response_model=AdminVideoPageOut)
async def list_videos(
    _: CurrentAdmin,
    session: ReadSession,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    removed: bool | None = Query(default=None, description="Only removed (true) or live (false)"),
) -> AdminVideoPageOut:
    """Every video regardless of owner, visibility or status, newest first."""
    after = parse_cursor(cursor)
    statement = select(Video, User).join(User, User.id == Video.owner_id)
    if removed is not None:
        statement = statement.where(
            Video.removed_at.is_not(None) if removed else Video.removed_at.is_(None)
        )
    if after is not None:
        statement = statement.where(
            tuple_(Video.created_at, Video.id)
            < tuple_(literal(after.created_at), literal(after.video_id))
        )
    statement = statement.order_by(desc(Video.created_at), desc(Video.id)).limit(limit + 1)
    rows = list((await session.execute(statement)).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1][0]
        next_cursor = encode_cursor(last.created_at, last.id)
    return AdminVideoPageOut(
        items=[
            _admin_video(VideoRecord.from_row(video, owner.display_name), owner)
            for video, owner in rows
        ],
        next_cursor=next_cursor,
    )


async def _locked_video(session: AsyncSession, video_ref: str) -> tuple[Video, User]:
    row = (
        await session.execute(
            select(Video, User)
            .join(User, User.id == Video.owner_id)
            .where(video_ref_filter(video_ref))
            .with_for_update(of=Video)
        )
    ).first()
    if row is None:
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")
    video, owner = row
    return video, owner


def _audit(admin: User, action: str, target_type: str, target_id: str, reason: str) -> AuditLog:
    return AuditLog(
        actor_id=admin.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
    )


@router.post("/videos/{video_ref}/takedown", response_model=AdminVideoOut)
async def take_down_video(
    video_ref: str,
    body: ReasonIn,
    admin: CurrentAdmin,
    session: WriteSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> AdminVideoOut:
    """Hide a video from everyone but admins. The owner sees the reason."""
    video, owner = await _locked_video(session, video_ref)
    if not can(admin, "takedown", video):
        raise AppError(409, "VIDEO_ALREADY_REMOVED", "This video is already taken down.")
    video.removed_at = func.now()
    video.removed_by = admin.id
    video.removal_reason = body.reason
    session.add(_audit(admin, "video.takedown", "video", video.public_id, body.reason))
    await session.commit()
    await session.refresh(video)
    await mark_user_wrote(redis, admin.id, settings.rw_flag_ttl_s)
    await cache.publish_video_change(redis, session, video.public_id)
    logger.info("Admin %s took down video %s", admin.id, video.public_id)
    return _admin_video(VideoRecord.from_row(video, owner.display_name), owner)


@router.post("/videos/{video_ref}/restore", response_model=AdminVideoOut)
async def restore_video(
    video_ref: str,
    body: ReasonIn,
    admin: CurrentAdmin,
    session: WriteSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> AdminVideoOut:
    """Undo a takedown. The video returns with the visibility it had before."""
    video, owner = await _locked_video(session, video_ref)
    if not can(admin, "restore", video):
        raise AppError(409, "VIDEO_NOT_REMOVED", "This video is not taken down.")
    video.removed_at = None
    video.removed_by = None
    video.removal_reason = None
    session.add(_audit(admin, "video.restore", "video", video.public_id, body.reason))
    await session.commit()
    await session.refresh(video)
    await mark_user_wrote(redis, admin.id, settings.rw_flag_ttl_s)
    await cache.publish_video_change(redis, session, video.public_id)
    logger.info("Admin %s restored video %s", admin.id, video.public_id)
    return _admin_video(VideoRecord.from_row(video, owner.display_name), owner)


@router.post("/users/{user_id}/disable", response_model=AdminUserOut)
async def disable_user(
    user_id: uuid.UUID,
    body: ReasonIn,
    admin: CurrentAdmin,
    session: WriteSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> AdminUserOut:
    """Block the account from logging in and sign it out everywhere.

    Refresh tokens are revoked at once. An access token already issued stays
    valid for at most its 15 minutes, but every API route that needs a user
    re-reads the account and refuses a disabled one.
    """
    if not can(admin, "disable_user"):
        raise AppError(403, "FORBIDDEN", "Admins only.")
    if user_id == admin.id:
        raise AppError(400, "CANNOT_DISABLE_SELF", "You cannot disable your own account.")
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise AppError(404, "USER_NOT_FOUND", "That account does not exist.")
    if user.disabled_at is not None:
        raise AppError(409, "USER_ALREADY_DISABLED", "This account is already disabled.")
    user.disabled_at = func.now()
    session.add(_audit(admin, "user.disable", "user", str(user.id), body.reason))
    await session.commit()
    await session.refresh(user)
    await mark_user_wrote(redis, admin.id, settings.rw_flag_ttl_s)
    await RefreshTokenStore(redis, settings.refresh_token_ttl_s).revoke_all(user.id)
    logger.info("Admin %s disabled user %s", admin.id, user.id)
    return AdminUserOut.model_validate(user)


@router.get("/audit", response_model=list[AuditEntryOut])
async def audit_log(
    _: CurrentAdmin,
    session: ReadSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AuditEntryOut]:
    """The most recent admin actions, newest first."""
    rows = (
        await session.execute(
            select(AuditLog, User.display_name)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .order_by(desc(AuditLog.id))
            .limit(limit)
        )
    ).all()
    return [
        AuditEntryOut(
            id=entry.id,
            actor_name=actor_name,
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            reason=entry.reason,
            created_at=entry.created_at,
        )
        for entry, actor_name in rows
    ]
