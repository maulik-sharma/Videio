from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    ReadSession,
    RedisDep,
    RefreshStoreDep,
    SettingsDep,
    WriteSession,
    client_ip,
)
from app.api.schemas import LoginIn, RegisterIn, UserOut
from app.core.config import Settings
from app.core.errors import AppError, error_body
from app.core.security import (
    create_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.db.models import User
from app.services import rate_limit
from app.services.read_your_writes import mark_user_wrote

router = APIRouter(prefix="/api/auth", tags=["auth"])

# The refresh cookie is sent only to /api/auth/* (refresh and logout), never to
# the rest of the API, so as few handlers as possible ever see it.
REFRESH_COOKIE_PATH = "/api/auth"

RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def _set_session_cookies(
    response: Response, settings: Settings, user: User, refresh_token: str
) -> None:
    access = create_access_token(
        user.id, user.role, settings.jwt_secret.get_secret_value(), settings.access_token_ttl_s
    )
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_ttl_s,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_ttl_s,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_COOKIE_PATH)):
        response.delete_cookie(
            name, path=path, httponly=True, secure=settings.cookie_secure, samesite="lax"
        )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn,
    request: Request,
    session: WriteSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> UserOut:
    """Create an account. Does not log in; call /login next."""
    await rate_limit.enforce(
        redis,
        f"register:ip:{client_ip(request)}",
        rate_limit.Limit(settings.register_limit_per_ip, settings.register_window_s),
    )
    user = User(
        email=str(body.email),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(409, "EMAIL_TAKEN", "An account with this email already exists.") from exc
    await session.refresh(user)
    await mark_user_wrote(redis, user.id, settings.rw_flag_ttl_s)
    return UserOut.model_validate(user)


@router.post("/login")
async def login(
    body: LoginIn,
    response: Response,
    session: WriteSession,
    redis: RedisDep,
    sessions: RefreshStoreDep,
    settings: SettingsDep,
) -> UserOut:
    """Verify credentials and set the access and refresh cookies."""
    email = str(body.email).lower()
    await rate_limit.enforce(
        redis,
        f"login:email:{email}",
        rate_limit.Limit(settings.login_limit_per_email, settings.login_window_s),
    )
    # Login reads the primary: an account registered a moment ago may not have
    # reached the replica yet, and a login must never fail because of lag.
    user = await session.scalar(select(User).where(User.email == email))
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        raise AppError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
    if user.disabled_at is not None:
        raise AppError(403, "ACCOUNT_DISABLED", "This account has been disabled.")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        await session.commit()

    issued = await sessions.issue(user.id)
    _set_session_cookies(response, settings, user, issued.token)
    return UserOut.model_validate(user)


@router.post("/refresh", response_model=UserOut)
async def refresh(
    response: Response,
    session: ReadSession,
    sessions: RefreshStoreDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> Any:
    """Rotate the refresh token and issue a new access token.

    On failure the response also clears both cookies, so the browser stops
    presenting a session that can no longer be refreshed.
    """
    try:
        if not refresh_token:
            raise AppError(401, "INVALID_REFRESH_TOKEN", "Session expired. Please log in again.")
        issued = await sessions.rotate(refresh_token)
        user = await session.get(User, issued.user_id)
        if user is None or user.disabled_at is not None:
            await sessions.revoke_all(issued.user_id)
            raise AppError(401, "INVALID_REFRESH_TOKEN", "Session expired. Please log in again.")
    except AppError as exc:
        failed = JSONResponse(error_body(exc.code, exc.message), exc.status)
        _clear_session_cookies(failed, settings)
        return failed

    _set_session_cookies(response, settings, user, issued.token)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    sessions: RefreshStoreDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> None:
    """Revoke this session's refresh token and clear both cookies. Idempotent."""
    if refresh_token:
        await sessions.revoke(refresh_token)
    _clear_session_cookies(response, settings)
