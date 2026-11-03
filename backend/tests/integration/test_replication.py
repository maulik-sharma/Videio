"""Read/write routing and the read-your-writes flag, against a real streaming replica."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text

from app.db.engines import Database
from tests.integration.conftest import login, register, wait_for_replica


@asynccontextmanager
async def replica_replay_paused(db: Database) -> AsyncIterator[None]:
    """Freeze the replica so it falls behind the primary, then resume it."""
    async with db.read_engine.connect() as conn:
        await conn.execute(text("SELECT pg_wal_replay_pause()"))
        while await conn.scalar(text("SELECT pg_get_wal_replay_pause_state()")) != "paused":
            pass
    try:
        yield
    finally:
        async with db.read_engine.connect() as conn:
            await conn.execute(text("SELECT pg_wal_replay_resume()"))


async def test_reads_follow_the_users_own_writes_while_the_replica_lags(
    client: AsyncClient, db: Database, redis: Redis
) -> None:
    async with replica_replay_paused(db):
        account = await register(client)  # written to the primary only
        await login(client, account)

        # The write set rw:{user}, so this read goes to the primary and succeeds.
        assert await redis.exists(f"rw:{account.id}")
        assert (await client.get("/api/me")).status_code == 200

        # Without the flag the read goes to the replica, which has not seen the user.
        await redis.delete(f"rw:{account.id}")
        response = await client.get("/api/me")
        assert response.status_code == 401

    await wait_for_replica(db)
    assert (await client.get("/api/me")).status_code == 200


async def test_login_works_even_before_the_replica_has_the_account(
    client: AsyncClient, db: Database
) -> None:
    async with replica_replay_paused(db):
        account = await register(client)
        response = await client.post(
            "/api/auth/login", json={"email": account.email, "password": account.password}
        )
        assert response.status_code == 200


async def test_read_your_writes_flag_expires(client: AsyncClient, redis: Redis) -> None:
    account = await register(client)
    ttl = await redis.ttl(f"rw:{account.id}")
    assert 0 < ttl <= 5
