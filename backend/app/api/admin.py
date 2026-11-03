from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import CurrentAdmin, DbDep

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
