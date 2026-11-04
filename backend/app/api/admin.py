from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
import uuid

from app.api.deps import CurrentAdmin, DbDep, WriteSession
from app.services.audit import log_action

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


class SystemStatus(BaseModel):
    replication: ReplicationStatus


@router.get("/system")
async def system(_: CurrentAdmin, db: DbDep) -> SystemStatus:
    """Operational numbers for the admin System panel. Queues and caches arrive later."""
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

    return SystemStatus(
        replication=ReplicationStatus(
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
    )


class TakedownIn(BaseModel):
    reason: str


class UserDisableIn(BaseModel):
    reason: str | None = None


@router.post("/videos/{video_ref}/takedown")
async def admin_takedown(
    video_ref: str,
    body: TakedownIn,
    _: CurrentAdmin,
    session: WriteSession,
) -> None:
    """Admin takedown of any video. Writes to audit_log."""
    video = await session.scalar(
        select(Video).where(_video_ref_filter(video_ref))
    )
    if video is None:
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")

    video.removed_at = func.now()
    video.removed_by = None  # admin, not owner
    video.removal_reason = body.reason
    await log_action(
        session=session,
        actor_id=None,
        action="video.takedown",
        target_type="video",
        target_id=video.id,
        reason=body.reason,
    )
    await session.commit()


@router.post("/videos/{video_ref}/restore")
async def admin_restore(
    video_ref: str,
    _: CurrentAdmin,
    session: WriteSession,
) -> None:
    """Admin restore of a takedown video."""
    video = await session.scalar(
        select(Video).where(_video_ref_filter(video_ref))
    )
    if video is None:
        raise AppError(404, "VIDEO_NOT_FOUND", "That video does not exist.")

    video.removed_at = None
    video.removed_by = None
    video.removal_reason = None
    await session.commit()


@router.post("/admin/users/{user_id}/disable")
async def admin_disable_user(
    user_id: uuid.UUID,
    body: UserDisableIn,
    _: CurrentAdmin,
    session: WriteSession,
) -> None:
    """Disable a user account and revoke all refresh tokens."""
    from app.db.models import User as _User

    user = await session.get(_User, user_id)
    if user is None:
        raise AppError(404, "USER_NOT_FOUND", "That user does not exist.")

    user.disabled_at = func.now()
    await log_action(
        session=session,
        actor_id=None,
        action="user.disable",
        target_type="user",
        target_id=user.id,
        reason=body.reason or "disabled by admin",
    )
    await session.commit()
