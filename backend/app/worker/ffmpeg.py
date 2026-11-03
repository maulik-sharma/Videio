"""Async ffprobe/ffmpeg helpers for the fixed Videio HLS ladder."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PermanentTranscodeError(RuntimeError):
    """The source cannot produce a valid video and should not be retried."""


class RetryableTranscodeError(RuntimeError):
    """A temporary worker or dependency error that should be retried."""


@dataclass(frozen=True)
class MediaProbe:
    duration_s: float
    width: int
    height: int


@dataclass(frozen=True)
class Rendition:
    name: str
    height: int
    bitrate_kbps: int


async def _run(command: list[str], *, timeout_s: float) -> tuple[bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        if "process" in locals():
            process.kill()
            await process.communicate()
        raise RetryableTranscodeError("The media command timed out.") from exc
    except OSError as exc:
        raise RetryableTranscodeError("The media command could not be started.") from exc

    if process.returncode != 0:
        detail = stderr.decode(errors="replace")[-2000:]
        raise PermanentTranscodeError(detail or "The media command rejected the source.")
    return stdout, stderr


async def probe_media(source: Path) -> MediaProbe:
    stdout, _ = await _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        timeout_s=60,
    )
    try:
        payload: dict[str, Any] = json.loads(stdout)
        streams = payload.get("streams", [])
        video_stream = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            None,
        )
        if not isinstance(video_stream, dict):
            raise PermanentTranscodeError("The file has no usable video stream.")
        duration_raw = payload.get("format", {}).get("duration")
        if duration_raw is None:
            duration_raw = video_stream.get("duration")
        duration = float(duration_raw)
        width = int(video_stream["width"])
        height = int(video_stream["height"])
    except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError) as exc:
        raise PermanentTranscodeError("The file has no usable video stream.") from exc

    if duration <= 0:
        raise PermanentTranscodeError("The file has no usable duration.")
    if duration > 10 * 60:
        raise PermanentTranscodeError("Videos must be 10 minutes or shorter.")
    return MediaProbe(duration_s=duration, width=width, height=height)


def renditions_for_height(source_height: int) -> list[Rendition]:
    renditions = [Rendition("360p", 360, 800)]
    if source_height >= 720:
        renditions.append(Rendition("720p", 720, 2800))
    return renditions


def hls_command(source: Path, output: Path, rendition: Rendition) -> list[str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"scale=-2:{rendition.height}:force_original_aspect_ratio=decrease",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        f"{rendition.bitrate_kbps}k",
        "-maxrate",
        f"{int(rendition.bitrate_kbps * 1.1)}k",
        "-bufsize",
        f"{rendition.bitrate_kbps * 2}k",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-f",
        "hls",
        "-hls_time",
        "6",
        "-hls_playlist_type",
        "vod",
        "-hls_segment_filename",
        str(output.parent / "segment_%03d.ts"),
        str(output),
    ]


def thumbnail_command(source: Path, output: Path, duration_s: float) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        f"{duration_s * 0.1:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output),
    ]


def write_master_playlist(output: Path, probe: MediaProbe, renditions: list[Rendition]) -> None:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for rendition in renditions:
        height = min(rendition.height, probe.height)
        width = max(2, round(probe.width * height / probe.height / 2) * 2)
        bandwidth = (rendition.bitrate_kbps + 128) * 1000
        lines.extend(
            [
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height}",
                f"{rendition.name}/index.m3u8",
            ]
        )
    output.write_text("\n".join(lines) + "\n")


async def transcode_hls(
    source: Path, workdir: Path, probe: MediaProbe
) -> tuple[Path, Path, list[Rendition]]:
    renditions = renditions_for_height(probe.height)
    for rendition in renditions:
        await _run(
            hls_command(source, workdir / rendition.name / "index.m3u8", rendition),
            timeout_s=15 * 60,
        )

    master = workdir / "master.m3u8"
    write_master_playlist(master, probe, renditions)
    thumbnail = workdir / "thumbnail.jpg"
    await _run(thumbnail_command(source, thumbnail, probe.duration_s), timeout_s=5 * 60)
    return master, thumbnail, renditions
