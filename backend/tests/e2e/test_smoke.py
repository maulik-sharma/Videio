"""Smoke test for the real edge -> API -> storage -> worker -> HLS path."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import httpx

EDGE = "http://localhost:8080"
RABBIT_API = "http://localhost:15672/api"
PASSWORD = "correct horse battery"
HEADERS = {"X-Requested-With": "videio"}


async def _upload(client: httpx.AsyncClient, payload: bytes, title: str) -> str:
    created = await client.post(
        "/api/videos",
        headers=HEADERS,
        json={
            "title": title,
            "description": "CI smoke test",
            "visibility": "public",
            "size_bytes": len(payload),
            "content_type": "video/mp4",
        },
    )
    assert created.status_code == 201, created.text
    upload = created.json()
    assert len(upload["parts"]) == 1

    part = await client.put(upload["parts"][0]["url"], content=payload)
    assert part.status_code in {200, 201, 204}, part.text
    etag = part.headers.get("etag")
    assert etag

    completed = await client.post(
        f"/api/videos/{upload['public_id']}/complete",
        headers=HEADERS,
        json={"parts": [{"part_number": 1, "etag": etag}]},
    )
    assert completed.status_code == 202, completed.text
    return upload["public_id"]


async def _wait_for_status(client: httpx.AsyncClient, public_id: str, expected: str) -> dict:
    for _ in range(120):
        response = await client.get(f"/api/videos/{public_id}")
        if response.status_code == 200:
            body = response.json()
            if body["status"] == expected:
                return body
            if body["status"] == "failed" and expected != "failed":
                raise AssertionError(body)
        await asyncio.sleep(1)
    raise AssertionError(f"video {public_id} did not reach {expected}")


async def test_upload_transcodes_and_corrupt_upload_reaches_dlq(tmp_path: Path) -> None:
    clip = tmp_path / "smoke.mp4"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=3:size=640x360:rate=24",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:duration=3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(clip),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode(errors="replace")

    async with httpx.AsyncClient(base_url=EDGE, timeout=30) as client:
        account = f"smoke-{uuid.uuid4().hex}@example.com"
        registered = await client.post(
            "/api/auth/register",
            headers=HEADERS,
            json={"email": account, "password": PASSWORD, "display_name": "CI smoke"},
        )
        assert registered.status_code == 201, registered.text
        logged_in = await client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"email": account, "password": PASSWORD},
        )
        assert logged_in.status_code == 200, logged_in.text

        ready_id = await _upload(client, clip.read_bytes(), "CI valid video")
        ready = await _wait_for_status(client, ready_id, "ready")
        playlist = await client.get(ready["playback_url"])
        assert playlist.status_code == 200, playlist.text
        assert playlist.text.startswith("#EXTM3U")

        failed_id = await _upload(client, b"not a video", "CI corrupt video")
        failed = await _wait_for_status(client, failed_id, "failed")
        assert failed["failure_reason"]

    async with httpx.AsyncClient(
        base_url=RABBIT_API,
        auth=("videio", "change-me-rabbit"),
        timeout=10,
    ) as rabbit:
        queue = await rabbit.get("/queues/%2F/transcode.dlq")
        assert queue.status_code == 200, queue.text
        assert queue.json()["messages"] >= 1
