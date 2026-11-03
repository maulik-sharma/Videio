"""Password hashing, access-token JWTs and opaque refresh tokens."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
# Verified against when the email is unknown, so a miss costs the same time as a hit
# and response timing does not reveal which emails are registered.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    role: str


def create_access_token(user_id: uuid.UUID, role: str, secret: str, ttl_s: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, secret: str) -> AccessClaims | None:
    """Return the claims, or None for any invalid, tampered or expired token."""
    try:
        payload = jwt.decode(
            token, secret, algorithms=[JWT_ALGORITHM], options={"require": ["sub", "exp"]}
        )
        if payload.get("typ") != "access":
            return None
        return AccessClaims(user_id=uuid.UUID(payload["sub"]), role=str(payload.get("role")))
    except (jwt.PyJWTError, ValueError):
        return None


def new_refresh_token() -> str:
    """An opaque, unguessable token (256 bits). Only its hash is stored server-side."""
    return secrets.token_urlsafe(32)


def refresh_token_id(token: str) -> str:
    """Redis key id for a refresh token: a hash, so a Redis dump yields no usable tokens."""
    return hashlib.sha256(token.encode()).hexdigest()
