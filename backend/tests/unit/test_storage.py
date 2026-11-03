from typing import Any

from app.core.config import Settings
from app.services.storage import S3Storage


class FakeClient:
    async def create_multipart_upload(self, **_: Any) -> dict[str, str]:
        return {"UploadId": "upload-1"}

    async def generate_presigned_url(self, operation: str, **_: Any) -> str:
        return f"https://signed.test/{operation}"


class FakeClientContext:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    async def __aenter__(self) -> FakeClient:
        return self.client

    async def __aexit__(self, *_: Any) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.endpoints: list[str] = []

    def client(self, _service: str, *, endpoint_url: str, **_: Any) -> FakeClientContext:
        self.endpoints.append(endpoint_url)
        return FakeClientContext(FakeClient())


async def test_storage_uses_internal_endpoint_for_writes_and_public_for_signing() -> None:
    settings = Settings(
        jwt_secret="x" * 32,
        s3_internal_endpoint_url="http://seaweedfs:8333",
        s3_public_endpoint_url="https://s3.example.test",
    )
    storage = S3Storage.from_settings(settings)
    session = FakeSession()
    storage._session = session  # type: ignore[assignment]

    assert await storage.create_multipart_upload("originals/abc", "video/mp4") == "upload-1"
    assert await storage.presign_upload_parts("originals/abc", "upload-1", 2) == [
        "https://signed.test/upload_part",
        "https://signed.test/upload_part",
    ]
    assert session.endpoints == ["http://seaweedfs:8333", "https://s3.example.test"]
