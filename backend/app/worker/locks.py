"""A small Redis lock: SET NX with a TTL, released only by its holder."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

# Delete the key only if it still holds our token, so an expired lock that
# someone else has since taken is never released by the old holder.
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


@asynccontextmanager
async def redis_lock(redis: Redis, key: str, ttl_ms: int) -> AsyncIterator[bool]:
    """Yield True if the lock was taken (and release it afterwards), else False."""
    token = secrets.token_urlsafe(18)
    acquired = bool(await redis.set(key, token, nx=True, px=ttl_ms))
    try:
        yield acquired
    finally:
        if acquired:
            await redis.eval(_RELEASE, 1, key, token)
