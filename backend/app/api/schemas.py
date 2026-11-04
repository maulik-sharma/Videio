import uuid
from datetime import datetime
from math import ceil
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
VideoTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
VideoDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]

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
    owner_id: uuid.UUID
    owner_name: str
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
    # Set only on videos an admin took down; only the owner and admins see those.
    removed_at: datetime | None = None
    removal_reason: str | None = None


class VideoPageOut(BaseModel):
    items: list[VideoSummaryOut]
    next_cursor: str | None


class VideoUpdateIn(BaseModel):
    """Owner edits. Send only the fields that change."""

    model_config = ConfigDict(extra="forbid")

    title: VideoTitle | None = None
    description: VideoDescription | None = None
    visibility: Visibility | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "VideoUpdateIn":
        if not self.model_fields_set:
            raise ValueError("Send at least one of title, description or visibility.")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class ReasonIn(BaseModel):
    """Why an admin acted. Stored in the audit log; takedown reasons reach the owner."""

    reason: Reason


class AdminVideoOut(VideoSummaryOut):
    owner_email: str
    owner_disabled: bool


class AdminVideoPageOut(BaseModel):
    items: list[AdminVideoOut]
    next_cursor: str | None


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: Literal["user", "admin"]
    disabled_at: datetime | None


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_name: str | None
    action: str
    target_type: Literal["video", "user"]
    target_id: str
    reason: str
    created_at: datetime


def expected_part_count(size_bytes: int, part_size_bytes: int) -> int:
    return ceil(size_bytes / part_size_bytes)
