"""Small S3-compatible storage wrapper used by the API and workers.

All S3 access lives here so the rest of the application does not depend on
aioboto3 details. SeaweedFS is addressed path-style because the browser-facing
presigned URL must use the exact public host that was signed.
"""

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aioboto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from app.core.config import Settings


class StorageError(RuntimeError):
    """An object-storage operation failed or returned an unusable response."""


class StorageVerificationError(StorageError):
    """Storage could not verify an object after a multipart completion."""


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self.internal_endpoint_url = settings.s3_internal_endpoint_url
        self.public_endpoint_url = settings.s3_public_endpoint_url
        self.access_key = settings.s3_access_key.get_secret_value()
        self.secret_key = settings.s3_secret_key.get_secret_value()
        self.region = settings.s3_region
        self.upload_bucket = settings.s3_upload_bucket
        self.media_bucket = settings.s3_media_bucket
        self.presign_ttl_s = settings.s3_presign_ttl_s
        self.app_origin = settings.app_origin
        self._session = aioboto3.Session()
        self._client_config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3Storage":
        return cls(settings)

    @asynccontextmanager
    async def _client(self, *, public: bool = False) -> AsyncIterator[Any]:
        endpoint = self.public_endpoint_url if public else self.internal_endpoint_url
        async with self._session.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=self._client_config,
        ) as client:
            yield client

    async def create_multipart_upload(self, key: str, content_type: str) -> str:
        try:
            async with self._client() as client:
                response = await client.create_multipart_upload(
                    Bucket=self.upload_bucket,
                    Key=key,
                    ContentType=content_type,
                )
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage could not start the upload.") from exc
        upload_id = response.get("UploadId")
        if not isinstance(upload_id, str) or not upload_id:
            raise StorageError("Object storage did not return an upload id.")
        return upload_id

    async def presign_upload_parts(self, key: str, upload_id: str, part_count: int) -> list[str]:
        try:
            async with self._client(public=True) as client:
                return [
                    await client.generate_presigned_url(
                        "upload_part",
                        Params={
                            "Bucket": self.upload_bucket,
                            "Key": key,
                            "UploadId": upload_id,
                            "PartNumber": part_number,
                        },
                        ExpiresIn=self.presign_ttl_s,
                        HttpMethod="PUT",
                    )
                    for part_number in range(1, part_count + 1)
                ]
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage could not sign the upload.") from exc

    async def complete_multipart_upload(
        self, key: str, upload_id: str, parts: Iterable[dict[str, Any]]
    ) -> None:
        try:
            async with self._client() as client:
                await client.complete_multipart_upload(
                    Bucket=self.upload_bucket,
                    Key=key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": list(parts)},
                )
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage rejected the uploaded parts.") from exc

    async def object_size(self, key: str) -> int:
        try:
            async with self._client() as client:
                response = await client.head_object(Bucket=self.upload_bucket, Key=key)
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageVerificationError(
                "Object storage could not verify the uploaded object."
            ) from exc
        size = response.get("ContentLength")
        if not isinstance(size, int):
            raise StorageVerificationError("Object storage returned no object size.")
        return size

    async def delete_object(self, key: str) -> None:
        try:
            async with self._client() as client:
                await client.delete_object(Bucket=self.upload_bucket, Key=key)
        except (BotoCoreError, ClientError, OSError, TimeoutError):
            return

    async def delete_upload(self, key: str) -> None:
        """Delete an original. Unlike delete_object, failures raise: callers must know."""
        try:
            async with self._client() as client:
                await client.delete_object(Bucket=self.upload_bucket, Key=key)
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage could not delete the original.") from exc

    async def delete_media_prefix(self, prefix: str) -> int:
        """Delete every generated object under `prefix`. Returns how many were deleted."""
        deleted = 0
        try:
            async with self._client() as client:
                paginator = client.get_paginator("list_objects_v2")
                async for page in paginator.paginate(Bucket=self.media_bucket, Prefix=prefix):
                    keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                    if not keys:
                        continue
                    response = await client.delete_objects(
                        Bucket=self.media_bucket, Delete={"Objects": keys, "Quiet": True}
                    )
                    if response.get("Errors"):
                        raise StorageError("Object storage could not delete some media files.")
                    deleted += len(keys)
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage could not delete media files.") from exc
        return deleted

    async def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """Abort an upload as best effort after a failed API transaction."""
        try:
            async with self._client() as client:
                await client.abort_multipart_upload(
                    Bucket=self.upload_bucket,
                    Key=key,
                    UploadId=upload_id,
                )
        except (BotoCoreError, ClientError, OSError, TimeoutError):
            # The cleanup scheduler in Phase 3 will retry abandoned uploads.
            return

    async def download_original(self, key: str, destination: Path) -> None:
        """Stream an uploaded original to a local worker file."""
        try:
            async with self._client() as client:
                response = await client.get_object(Bucket=self.upload_bucket, Key=key)
                body = response["Body"]
                try:
                    with destination.open("wb") as output:
                        while chunk := await body.read(1024 * 1024):
                            output.write(chunk)
                finally:
                    body.close()
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage could not download the original.") from exc

    async def upload_original_from_path(self, source: Path, key: str, content_type: str) -> None:
        """Upload a local file directly to the uploads bucket (used by the seed script)."""
        try:
            async with self._client() as client:
                await client.upload_file(
                    str(source),
                    self.upload_bucket,
                    key,
                    ExtraArgs={"ContentType": content_type},
                )
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage could not upload the original file.") from exc

    async def upload_media_file(self, source: Path, key: str, content_type: str) -> None:
        """Upload a generated media file to the immutable media bucket."""
        try:
            async with self._client() as client:
                await client.upload_file(
                    str(source),
                    self.media_bucket,
                    key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "CacheControl": "public, max-age=604800",
                    },
                )
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage could not upload generated media.") from exc

    async def ensure_buckets(self) -> None:
        """Create the application buckets and apply browser CORS idempotently."""
        try:
            async with self._client() as client:
                for bucket in (self.upload_bucket, self.media_bucket):
                    try:
                        await client.head_bucket(Bucket=bucket)
                    except ClientError as exc:
                        code = str(exc.response.get("Error", {}).get("Code", ""))
                        if code not in {"404", "NoSuchBucket", "NotFound"}:
                            raise
                        await client.create_bucket(Bucket=bucket)

                    await client.put_bucket_cors(
                        Bucket=bucket,
                        CORSConfiguration={
                            "CORSRules": [
                                {
                                    "AllowedOrigins": [self.app_origin],
                                    "AllowedMethods": ["PUT", "POST", "GET", "HEAD"],
                                    "AllowedHeaders": ["*"],
                                    "ExposeHeaders": ["ETag"],
                                    "MaxAgeSeconds": 3600,
                                }
                            ]
                        },
                    )
        except (BotoCoreError, ClientError, OSError, TimeoutError) as exc:
            raise StorageError("Object storage buckets could not be initialised.") from exc
