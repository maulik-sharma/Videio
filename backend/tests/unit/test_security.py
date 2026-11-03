import uuid

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    refresh_token_id,
    verify_password,
)

SECRET = "unit-test-secret-0123456789abcdef0123"


def test_password_hash_round_trip() -> None:
    hashed = hash_password("correct horse")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse")
    assert not verify_password(hashed, "wrong horse")


def test_verify_without_a_hash_is_always_false() -> None:
    # Unknown email: still runs a real verification, and must never succeed.
    assert not verify_password(None, "anything")


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    claims = decode_access_token(create_access_token(user_id, "admin", SECRET, 60), SECRET)
    assert claims is not None
    assert claims.user_id == user_id
    assert claims.role == "admin"


def test_expired_access_token_is_rejected() -> None:
    token = create_access_token(uuid.uuid4(), "user", SECRET, -1)
    assert decode_access_token(token, SECRET) is None


def test_access_token_signed_with_another_secret_is_rejected() -> None:
    token = create_access_token(uuid.uuid4(), "user", "another-secret-0123456789abcdef0123", 60)
    assert decode_access_token(token, SECRET) is None


def test_garbage_access_token_is_rejected() -> None:
    assert decode_access_token("not.a.jwt", SECRET) is None


def test_refresh_token_id_is_a_stable_hash_not_the_token() -> None:
    assert refresh_token_id("abc") == refresh_token_id("abc")
    assert refresh_token_id("abc") != "abc"
    assert len(refresh_token_id("abc")) == 64
