"""A small Redis lock: SET NX with a TTL, released only by its holder.

With `heartbeat=True` the holder keeps extending the TTL while it runs, so the
TTL only has to cover the gap after a holder dies, not the longest job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Delete the key only if it still holds our token, so an expired lock that
# someone else has since taken is never released by the old holder.
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

_EXTEND = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


async def _keep_alive(redis: Redis, key: str, token: str, ttl_ms: int) -> None:
    """Extend the lock every third of its TTL until cancelled or lost."""
    while True:
        await asyncio.sleep(ttl_ms / 3000)
        try:
            extended = await redis.eval(_EXTEND, 1, key, token, ttl_ms)
        except RedisError:
            # One missed beat is fine: there are two more before the TTL runs out.
            logger.warning("Could not extend lock %s; will try again", key)
            continue
        if not extended:
            logger.warning("Lost lock %s (it expired or was taken)", key)
            return


@asynccontextmanager
async def redis_lock(
    redis: Redis, key: str, ttl_ms: int, *, heartbeat: bool = False
) -> AsyncIterator[bool]:
    """Yield True if the lock was taken (and release it afterwards), else False."""
    token = secrets.token_urlsafe(18)
    acquired = bool(await redis.set(key, token, nx=True, px=ttl_ms))
    beat = (
        asyncio.create_task(_keep_alive(redis, key, token, ttl_ms))
        if acquired and heartbeat
        else None
    )
    try:
        yield acquired
    finally:
        if beat is not None:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        if acquired:
            await redis.eval(_RELEASE, 1, key, token)
