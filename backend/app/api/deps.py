"""FastAPI dependencies: settings, Redis, DB sessions with read routing, current user."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.db.engines import Database
from app.db.models import User
from app.services.read_your_writes import user_recently_wrote
from app.services.sessions import RefreshTokenStore

ACCESS_COOKIE = "videio_at"
REFRESH_COOKIE = "videio_rt"

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


DbDep = Annotated[Database, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]


def get_refresh_store(redis: RedisDep, settings: SettingsDep) -> RefreshTokenStore:
    return RefreshTokenStore(redis, settings.refresh_token_ttl_s)


RefreshStoreDep = Annotated[RefreshTokenStore, Depends(get_refresh_store)]


async def db_write(db: DbDep) -> AsyncIterator[AsyncSession]:
    """A session on the primary. Use for every write and for reads that must be fresh."""
    async with db.write_sessions() as session:
        yield session


WriteSession = Annotated[AsyncSession, Depends(db_write)]


def access_user_id(request: Request, settings: SettingsDep) -> uuid.UUID | None:
    """User id from the access cookie, or None. Signature check only, no DB lookup."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return None
    claims = decode_access_token(token, settings.jwt_secret.get_secret_value())
    return claims.user_id if claims else None


AccessUserId = Annotated[uuid.UUID | None, Depends(access_user_id)]


async def db_read(db: DbDep, redis: RedisDep, user_id: AccessUserId) -> AsyncIterator[AsyncSession]:
    """A session on the replica, or on the primary while this user's rw flag is set."""
    use_primary = user_id is not None and await user_recently_wrote(redis, user_id)
    sessions = db.write_sessions if use_primary else db.read_sessions
    async with sessions() as session:
        session.info["routed_to"] = "primary" if use_primary else "replica"
        yield session


ReadSession = Annotated[AsyncSession, Depends(db_read)]


async def current_user(user_id: AccessUserId, session: ReadSession) -> User:
    if user_id is None:
        raise AppError(401, "NOT_AUTHENTICATED", "Log in to continue.")
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(401, "NOT_AUTHENTICATED", "Log in to continue.")
    if user.disabled_at is not None:
        raise AppError(403, "ACCOUNT_DISABLED", "This account has been disabled.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def current_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise AppError(403, "FORBIDDEN", "Admins only.")
    return user


CurrentAdmin = Annotated[User, Depends(current_admin)]


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers sets this from X-Forwarded-For, which the edge
    # overwrites with the real client address (see infra/nginx/nginx.conf).
    return request.client.host if request.client else "unknown"
