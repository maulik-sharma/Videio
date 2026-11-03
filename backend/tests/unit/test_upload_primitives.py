from datetime import UTC, datetime

import pytest

from app.core.cursors import InvalidCursor, decode_cursor, encode_cursor
from app.core.ids import PUBLIC_ID_LENGTH, new_public_id


def test_public_ids_are_base62_and_fixed_length() -> None:
    values = {new_public_id() for _ in range(100)}
    assert len(values) == 100
    assert all(len(value) == PUBLIC_ID_LENGTH for value in values)
    assert all(value.isascii() and value.isalnum() for value in values)


def test_video_cursor_round_trip() -> None:
    created_at = datetime(2026, 11, 3, 12, 30, 45, 123456, tzinfo=UTC)
    cursor = encode_cursor(created_at, 42)
    decoded = decode_cursor(cursor)
    assert decoded.created_at == created_at
    assert decoded.video_id == 42


@pytest.mark.parametrize("value", ["", "not-base64", "e30", "eyJpZCI6MCJ9"])
def test_invalid_video_cursor_is_rejected(value: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(value)
