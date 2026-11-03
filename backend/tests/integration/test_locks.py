"""The worker's Redis lock, against real Redis."""

import asyncio

from redis.asyncio import Redis

from app.worker.locks import redis_lock


async def test_only_one_holder_and_release_frees_it(redis: Redis) -> None:
    async with redis_lock(redis, "lock:test", 5_000) as first:
        assert first
        async with redis_lock(redis, "lock:test", 5_000) as second:
            assert not second
    async with redis_lock(redis, "lock:test", 5_000) as again:
        assert again


async def test_heartbeat_keeps_a_long_job_locked_past_its_ttl(redis: Redis) -> None:
    async with redis_lock(redis, "lock:test", 600, heartbeat=True) as held:
        assert held
        await asyncio.sleep(1.5)  # 2.5 TTLs: without the heartbeat it would have lapsed
        assert await redis.exists("lock:test")
    assert not await redis.exists("lock:test")


async def test_without_a_heartbeat_a_dead_holders_lock_lapses(redis: Redis) -> None:
    await redis.set("lock:test", "someone-who-died", px=300)
    await asyncio.sleep(0.5)
    async with redis_lock(redis, "lock:test", 5_000) as held:
        assert held


async def test_a_holder_never_releases_a_lock_it_lost(redis: Redis) -> None:
    async with redis_lock(redis, "lock:test", 5_000) as held:
        assert held
        await redis.set("lock:test", "someone-else")  # ours expired; theirs now
    assert await redis.get("lock:test") == "someone-else"
