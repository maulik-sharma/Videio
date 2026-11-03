import uuid
from datetime import datetime
from math import ceil
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
VideoTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
VideoDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]

Visibility = Literal["public", "unlisted", "private"]
VideoStatus = Literal["pending_upload", "uploaded", "processing", "ready", "failed"]
VideoContentType = Literal["video/mp4", "video/quicktime", "video/webm", "video/x-matroska"]


class RegisterIn(BaseModel):
    email: EmailStr
    password: Password
    display_name: DisplayName


class LoginIn(BaseModel):
    email: EmailStr
    # No length rules on login: a wrong password is just a wrong password.
    password: Annotated[str, StringConstraints(max_length=1024)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: Literal["user", "admin"]
    created_at: datetime


class VideoCreateIn(BaseModel):
    """Metadata supplied before the browser starts its direct S3 upload."""

    model_config = ConfigDict(populate_by_name=True)

    title: VideoTitle
    description: VideoDescription = ""
    visibility: Visibility = "public"
    size_bytes: int = Field(
        validation_alias=AliasChoices("size_bytes", "file_size", "file_size_bytes"),
        ge=1,
    )
    content_type: VideoContentType = Field(
        validation_alias=AliasChoices("content_type", "mime_type")
    )


class PresignedPartOut(BaseModel):
    part_number: int
    url: str


class VideoUploadOut(BaseModel):
    id: int
    public_id: str
    title: str
    description: str
    visibility: Visibility
    status: VideoStatus
    size_bytes: int
    content_type: str
    part_size_bytes: int
    parts: list[PresignedPartOut]


class CompletedPartIn(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=256)


class CompleteVideoIn(BaseModel):
    parts: list[CompletedPartIn] = Field(min_length=1, max_length=10_000)

    def has_unique_part_numbers(self) -> bool:
        return len({part.part_number for part in self.parts}) == len(self.parts)


class VideoSummaryOut(BaseModel):
    id: int
    public_id: str
    title: str
    description: str
    visibility: Visibility
    status: VideoStatus
    size_bytes: int
    content_type: str
    duration_s: float | None
    renditions: dict[str, object] | None
    thumbnail_key: str | None
    playback_url: str | None
    thumbnail_url: str | None
    view_count: int
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class VideoPageOut(BaseModel):
    items: list[VideoSummaryOut]
    next_cursor: str | None


def expected_part_count(size_bytes: int, part_size_bytes: int) -> int:
    return ceil(size_bytes / part_size_bytes)
