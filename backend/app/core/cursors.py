"""Opaque keyset cursors for the user's video list and the later public feed."""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime


class InvalidCursor(ValueError):
    """Raised when a client sends a malformed or unsafe pagination cursor."""


@dataclass(frozen=True)
class VideoCursor:
    created_at: datetime
    video_id: int


def encode_cursor(created_at: datetime, video_id: int) -> str:
    if (
        created_at.tzinfo is None
        or not isinstance(video_id, int)
        or isinstance(video_id, bool)
        or video_id <= 0
    ):
        raise ValueError("A cursor needs a timezone-aware timestamp and positive id.")
    payload = json.dumps(
        {"created_at": created_at.astimezone(UTC).isoformat(), "id": video_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(value: str) -> VideoCursor:
    try:
        if not value or len(value) > 512:
            raise ValueError
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(
            base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
        )
        created_at = datetime.fromisoformat(decoded["created_at"])
        video_id = decoded["id"]
        if (
            not isinstance(created_at, datetime)
            or created_at.tzinfo is None
            or not isinstance(video_id, int)
            or isinstance(video_id, bool)
            or video_id <= 0
        ):
            raise ValueError
    except (binascii.Error, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise InvalidCursor("Invalid pagination cursor.") from exc
    return VideoCursor(created_at=created_at, video_id=video_id)
