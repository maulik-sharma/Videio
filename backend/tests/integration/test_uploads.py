"""Direct browser-to-SeaweedFS multipart upload flow."""

import httpx
from httpx import AsyncClient

from tests.integration.conftest import login, register


async def test_multipart_upload_completes_without_api_file_bytes(client: AsyncClient) -> None:
    account = await register(client)
    await login(client, account)

    payload = b"small test video"
    created = await client.post(
        "/api/videos",
        json={
            "title": "Direct upload",
            "description": "Uploaded directly to storage",
            "visibility": "unlisted",
            "size_bytes": len(payload),
            "content_type": "video/mp4",
        },
    )
    assert created.status_code == 201, created.text
    upload = created.json()
    assert upload["status"] == "pending_upload"
    assert len(upload["parts"]) == 1

    async with httpx.AsyncClient() as storage_client:
        part_response = await storage_client.put(upload["parts"][0]["url"], content=payload)
    assert part_response.status_code in {200, 201, 204}, part_response.text
    etag = part_response.headers.get("etag")
    assert etag

    completed = await client.post(
        f"/api/videos/{upload['public_id']}/complete",
        json={"parts": [{"part_number": 1, "etag": etag}]},
    )
    assert completed.status_code == 202, completed.text
    assert completed.json()["status"] == "uploaded"
    assert completed.json()["owner_name"] == "Ann"

    listed = await client.get("/api/me/videos")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["public_id"] == upload["public_id"]
    assert listed.json()["items"][0]["owner_name"] == "Ann"

    # The owner can open their not-yet-ready video, with the uploader's name.
    detail = await client.get(f"/api/videos/{upload['public_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["owner_name"] == "Ann"


async def test_upload_rejects_unsupported_media_type(client: AsyncClient) -> None:
    account = await register(client)
    await login(client, account)
    response = await client.post(
        "/api/videos",
        json={
            "title": "Not a video",
            "size_bytes": 10,
            "content_type": "text/plain",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
