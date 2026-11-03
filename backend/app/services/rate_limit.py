"""Token-bucket rate limiting in Redis, atomic via a Lua script."""

import math
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.errors import AppError

# KEYS[1] bucket key. ARGV: capacity, refill rate (tokens per ms).
# Uses Redis server time so every API replica shares one clock.
# Returns {allowed (0/1), retry_after_ms}.
_TOKEN_BUCKET = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + (now - ts) * rate)
local allowed = 0
local retry_ms = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_ms = math.ceil((1 - tokens) / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate))
return {allowed, retry_ms}
"""


@dataclass(frozen=True)
class Limit:
    capacity: int
    window_s: int

    @property
    def tokens_per_ms(self) -> float:
        return self.capacity / (self.window_s * 1000)


async def enforce(redis: Redis, key: str, limit: Limit) -> None:
    """Take one token from bucket `key`; raise 429 with Retry-After when it is empty."""
    allowed, retry_ms = await redis.eval(
        _TOKEN_BUCKET, 1, f"rl:{key}", limit.capacity, repr(limit.tokens_per_ms)
    )
    if not allowed:
        retry_after = max(1, math.ceil(int(retry_ms) / 1000))
        raise AppError(
            429,
            "RATE_LIMITED",
            f"Too many attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
            extra={"retry_after": retry_after},
        )
