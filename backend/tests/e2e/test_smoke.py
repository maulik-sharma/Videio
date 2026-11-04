"""Smoke test for the real edge -> API -> storage -> worker -> HLS path."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx

EDGE = "http://localhost:8080"
WORKER_CONTAINER = "videio-worker-1"
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


async def _wait_for_status(
    client: httpx.AsyncClient, public_id: str, expected: str
) -> dict[str, Any]:
    for _ in range(120):
        response = await client.get(f"/api/videos/{public_id}")
        if response.status_code == 200:
            body: dict[str, Any] = response.json()
            if body["status"] == expected:
                return body
            if body["status"] == "failed" and expected != "failed":
                raise AssertionError(body)
        await asyncio.sleep(1)
    raise AssertionError(f"video {public_id} did not reach {expected}")


FFMPEG_ARGS = [
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
]


async def _make_test_clip(path: Path) -> Path:
    """A 3-second clip, from the host's ffmpeg or else the worker container's."""
    if shutil.which("ffmpeg"):
        command = ["ffmpeg", *FFMPEG_ARGS, str(path)]
        stdout_target = None
    else:
        # Fragmented MP4 can be written to a pipe; the worker image has ffmpeg.
        command = [
            "docker",
            "exec",
            WORKER_CONTAINER,
            "ffmpeg",
            *FFMPEG_ARGS,
            "-movflags",
            "frag_keyframe+empty_moov",
            "-f",
            "mp4",
            "pipe:1",
        ]
        stdout_target = path
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode(errors="replace")
    if stdout_target is not None:
        stdout_target.write_bytes(stdout)
    return path


async def _dead_lettered_video_ids(rabbit: httpx.AsyncClient) -> set[int]:
    """Peek at the DLQ (messages are requeued) and return the video ids in it."""
    response = await rabbit.post(
        "/queues/%2F/transcode.dlq/get",
        json={"count": 1000, "ackmode": "ack_requeue_true", "encoding": "auto"},
    )
    assert response.status_code == 200, response.text
    ids: set[int] = set()
    for message in response.json():
        body = json.loads(message["payload"])
        if body.get("task_name", "").endswith("transcode_video") and body.get("args"):
            ids.add(int(body["args"][0]))
    return ids


async def _check_media_access_at_the_edge(owner: httpx.AsyncClient, video: dict[str, Any]) -> None:
    """nginx asks the API before serving any media, and caches only non-private videos."""
    thumbnail = video["thumbnail_url"]
    async with httpx.AsyncClient(base_url=EDGE, timeout=30) as anonymous:
        first = await anonymous.get(thumbnail)
        second = await anonymous.get(thumbnail)
        assert first.status_code == second.status_code == 200
        assert second.headers["x-edge-cache"] == "HIT"
        # Shared caches in front of the edge must not skip the access check.
        assert second.headers["cache-control"].startswith("private")

        made_private = await owner.patch(
            f"/api/videos/{video['public_id']}", headers=HEADERS, json={"visibility": "private"}
        )
        assert made_private.status_code == 200, made_private.text
        # Already in the edge cache, but the check runs first, so it is refused.
        assert (await anonymous.get(thumbnail)).status_code == 403
        owner_copy = await owner.get(thumbnail)
        assert owner_copy.status_code == 200
        assert owner_copy.headers["x-edge-cache"] == "BYPASS"

        deleted = await owner.delete(f"/api/videos/{video['public_id']}", headers=HEADERS)
        assert deleted.status_code == 204, deleted.text
        assert (await owner.get(video["playback_url"])).status_code == 403


async def test_upload_transcodes_and_corrupt_upload_reaches_dlq(tmp_path: Path) -> None:
    clip = await _make_test_clip(tmp_path / "smoke.mp4")

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
        # Follow the ladder down to a segment, as the player would.
        base = ready["playback_url"].rsplit("/", 1)[0]
        variant = await client.get(f"{base}/360p/index.m3u8")
        assert variant.status_code == 200, variant.text
        segment_name = next(line for line in variant.text.splitlines() if line.endswith(".ts"))
        segment = await client.get(f"{base}/360p/{segment_name}")
        assert segment.status_code == 200
        assert segment.content[:1] == b"G"  # MPEG-TS sync byte
        thumbnail = await client.get(ready["thumbnail_url"])
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"] == "image/jpeg"

        await _check_media_access_at_the_edge(client, ready)

        failed_id = await _upload(client, b"not a video", "CI corrupt video")
        failed = await _wait_for_status(client, failed_id, "failed")
        # The uploader sees a short reason, not ffmpeg's stderr with worker paths.
        assert failed["failure_reason"] == "The file could not be read as a video."

    # The task marks the video failed before the DLQ publish, so allow a moment.
    async with httpx.AsyncClient(
        base_url=RABBIT_API, auth=("videio", "change-me-rabbit"), timeout=10
    ) as rabbit:
        for _ in range(20):
            if failed["id"] in await _dead_lettered_video_ids(rabbit):
                break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError(f"video {failed['id']} never reached transcode.dlq")
