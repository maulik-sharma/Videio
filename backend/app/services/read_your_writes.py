"""Read-your-writes on top of an asynchronously replicated read replica.

After a user writes, `rw:{userId}` exists for a few seconds; while it does, that
user's reads go to the primary, so they never see a replica that is a little behind
their own change. Everyone else keeps reading from the replica.
"""

import uuid

from redis.asyncio import Redis


async def mark_user_wrote(redis: Redis, user_id: uuid.UUID, ttl_s: int) -> None:
    await redis.set(f"rw:{user_id}", "1", ex=ttl_s)


async def user_recently_wrote(redis: Redis, user_id: uuid.UUID) -> bool:
    return bool(await redis.exists(f"rw:{user_id}"))
