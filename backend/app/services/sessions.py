"""Refresh tokens in Redis, rotated on every use, with reuse (theft) detection.

Keys:
  rt:{id}       -> user id, TTL = refresh lifetime. `id` is sha256(token).
  rt_used:{id}  -> user id, TTL = refresh lifetime. Tombstone for a rotated token.
  user_rt:{uid} -> set of live token ids, so all of a user's sessions can be revoked.

Presenting a token whose tombstone exists means it was already rotated, so either
the legitimate client or an attacker holds a copy: revoke every session of that user.
"""

import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.errors import AppError
from app.core.security import new_refresh_token, refresh_token_id


def _text(value: bytes | str) -> str:
    """The client decodes responses, but redis-py's types still allow bytes."""
    return value.decode() if isinstance(value, bytes) else value


@dataclass(frozen=True)
class IssuedToken:
    token: str
    user_id: uuid.UUID


class RefreshTokenStore:
    def __init__(self, redis: Redis, ttl_s: int) -> None:
        self.redis = redis
        self.ttl_s = ttl_s

    async def issue(self, user_id: uuid.UUID) -> IssuedToken:
        token = new_refresh_token()
        tid = refresh_token_id(token)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(f"rt:{tid}", str(user_id), ex=self.ttl_s)
            pipe.sadd(f"user_rt:{user_id}", tid)
            pipe.expire(f"user_rt:{user_id}", self.ttl_s)
            await pipe.execute()
        return IssuedToken(token=token, user_id=user_id)

    async def rotate(self, token: str) -> IssuedToken:
        """Consume `token` and issue its replacement. Raises 401 if invalid or reused."""
        tid = refresh_token_id(token)
        # GETDEL is atomic: of two concurrent refreshes with the same token, exactly
        # one gets the user id; the other sees the tombstone and trips reuse detection.
        owner = await self.redis.getdel(f"rt:{tid}")
        if owner is None:
            reused_by = await self.redis.get(f"rt_used:{tid}")
            if reused_by is not None:
                await self.revoke_all(uuid.UUID(_text(reused_by)))
                raise AppError(
                    401,
                    "REFRESH_TOKEN_REUSED",
                    "This session was already refreshed elsewhere. All sessions were "
                    "signed out; please log in again.",
                )
            raise AppError(401, "INVALID_REFRESH_TOKEN", "Session expired. Please log in again.")

        user_id = uuid.UUID(_text(owner))
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(f"rt_used:{tid}", owner, ex=self.ttl_s)
            pipe.srem(f"user_rt:{user_id}", tid)
            await pipe.execute()
        return await self.issue(user_id)

    async def revoke(self, token: str) -> None:
        tid = refresh_token_id(token)
        owner = await self.redis.getdel(f"rt:{tid}")
        if owner is not None:
            await self.redis.srem(f"user_rt:{_text(owner)}", tid)

    async def revoke_all(self, user_id: uuid.UUID) -> None:
        tids = await self.redis.smembers(f"user_rt:{user_id}")
        async with self.redis.pipeline(transaction=True) as pipe:
            for tid in tids:
                pipe.delete(f"rt:{_text(tid)}")
            pipe.delete(f"user_rt:{user_id}")
            await pipe.execute()
