"""Async ffprobe/ffmpeg helpers for the Videio HLS ladder.

One ffmpeg run decodes the source once and encodes every rendition from it
(split filter, one HLS output per rung). The ladder holds each standard rung
up to the source, plus the source's own resolution when it is not a standard
rung; nothing is upscaled.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Standard rungs: short side in pixels -> video bitrate in kbps (H.264, ~30 fps).
LADDER_KBPS: dict[int, int] = {
    144: 150,
    240: 300,
    360: 700,
    480: 1200,
    720: 2500,
    1080: 5000,
    1440: 8000,
    2160: 14000,
}
MAX_RUNG = max(LADDER_KBPS)
AUDIO_KBPS = 128

# Thread counts are fixed rather than left to ffmpeg, which sizes them from the
# host's CPUs and ignores the container's limit. The rungs already encode in
# parallel, so one thread per encoder keeps the CPUs busy; extra threads only
# add frame buffers. Measured on a 4K source with 1.5 CPUs: 5.2 GB and 86 s
# with ffmpeg's defaults, 1.5 GB and 42 s with these.
DECODE_THREADS = 2
ENCODER_THREADS = 1

MAX_DURATION_S = 10 * 60
# The encode is stopped after (video length) x (pixels across all rungs, in
# 1080p frames) x this many seconds, and never sooner than the floor. Measured
# with 1.5 CPUs: about 0.6 s per 1080p-frame-second (a 1080p ladder ran at
# 1.2x the video's length, a 4K ladder at 4.4x), so 2 leaves about 3x headroom
# for a slower CPU or harder footage. A 10-minute video gets about 36 minutes
# at 1080p and about 2.5 hours at 4K.
TIMEOUT_S_PER_1080P_FRAME_SECOND = 2.0
MIN_TIMEOUT_S = 10 * 60
_1080P_PIXELS = 1920 * 1080
PROBE_TIMEOUT_S = 60
THUMBNAIL_TIMEOUT_S = 5 * 60


class PermanentTranscodeError(RuntimeError):
    """The source cannot produce a valid video and should not be retried.

    `str(exc)` is shown to the uploader; `detail` (e.g. ffmpeg's stderr) is only
    recorded on the transcode attempt, since it contains worker paths.
    """

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail or message


class RetryableTranscodeError(RuntimeError):
    """A temporary worker or dependency error that should be retried."""


@dataclass(frozen=True)
class MediaProbe:
    """The source as displayed: width and height already account for rotation."""

    duration_s: float
    width: int
    height: int

    @property
    def short_side(self) -> int:
        return min(self.width, self.height)

    @property
    def portrait(self) -> bool:
        return self.height > self.width


@dataclass(frozen=True)
class Rendition:
    name: str  # e.g. "720p", named by the short side
    width: int
    height: int
    bitrate_kbps: int


def transcode_timeout_s(duration_s: float, renditions: list[Rendition]) -> float:
    """How long the encode may run: proportional to the length and the pixels."""
    frames_of_1080p = sum(r.width * r.height for r in renditions) / _1080P_PIXELS
    return max(MIN_TIMEOUT_S, duration_s * frames_of_1080p * TIMEOUT_S_PER_1080P_FRAME_SECOND)


async def _run(command: list[str], *, timeout_s: float, rejected: str) -> tuple[bytes, bytes]:
    """Run a media command. `rejected` is the user-facing reason if it fails.

    A timeout is permanent: the same file on the same worker would only time
    out again, so retrying would just burn CPU. A process killed by a signal
    (e.g. the OOM killer) is the worker's problem, not the file's, so retry it.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise RetryableTranscodeError(f"{command[0]} could not be started.") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise PermanentTranscodeError(
            "The video took too long to process.",
            f"{command[0]} timed out after {timeout_s:.0f} s",
        ) from exc

    detail = stderr.decode(errors="replace")[-2000:]
    if process.returncode is not None and process.returncode < 0:
        raise RetryableTranscodeError(f"{command[0]} was killed by signal {-process.returncode}.")
    if process.returncode != 0:
        raise PermanentTranscodeError(rejected, detail)
    return stdout, stderr


def _rotation(stream: dict[str, Any]) -> int:
    """Degrees the player rotates the stream by (phone videos are often 90)."""
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            return int(float(side_data["rotation"]))
    return int(float((stream.get("tags") or {}).get("rotate", 0)))


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
        timeout_s=PROBE_TIMEOUT_S,
        rejected="The file could not be read as a video.",
    )
    try:
        payload: dict[str, Any] = json.loads(stdout)
        streams = payload.get("streams", [])
        video_stream = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            None,
        )
        if not isinstance(video_stream, dict):
            raise PermanentTranscodeError("The file has no video stream.")
        duration_raw = payload.get("format", {}).get("duration")
        if duration_raw is None:
            duration_raw = video_stream.get("duration")
        duration = float(duration_raw)
        width = int(video_stream["width"])
        height = int(video_stream["height"])
        # ffmpeg applies the rotation when encoding, so the ladder must too.
        if abs(_rotation(video_stream)) % 180 == 90:
            width, height = height, width
    except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError) as exc:
        raise PermanentTranscodeError("The file has no readable video stream.") from exc

    if width <= 0 or height <= 0:
        raise PermanentTranscodeError("The file has no readable video stream.")
    if duration <= 0:
        raise PermanentTranscodeError("The file has no usable duration.")
    if duration > MAX_DURATION_S:
        raise PermanentTranscodeError("Videos must be 10 minutes or shorter.")
    return MediaProbe(duration_s=duration, width=width, height=height)


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def _bitrate_for(short_side: int) -> int:
    """Bitrate for any short side: the table, interpolated between rungs."""
    if short_side in LADDER_KBPS:
        return LADDER_KBPS[short_side]
    rungs = sorted(LADDER_KBPS)
    if short_side < rungs[0]:
        return max(100, LADDER_KBPS[rungs[0]] * short_side // rungs[0])
    for low, high in itertools.pairwise(rungs):
        if low < short_side < high:
            span = (short_side - low) / (high - low)
            return round(LADDER_KBPS[low] + span * (LADDER_KBPS[high] - LADDER_KBPS[low]))
    return LADDER_KBPS[rungs[-1]]


def _rendition(probe: MediaProbe, short_side: int) -> Rendition:
    long_side = _even(max(probe.width, probe.height) * short_side / probe.short_side)
    width, height = (short_side, long_side) if probe.portrait else (long_side, short_side)
    return Rendition(f"{short_side}p", width, height, _bitrate_for(short_side))


def ladder_for(probe: MediaProbe) -> list[Rendition]:
    """Every standard rung up to the source, plus the source's own resolution.

    A 1080p source gets 1080p, 720p, 480p, 360p, 240p and 144p. A 900p source
    gets 900p (its original), then 720p and down. Sources above 2160p are
    capped at 2160p. Highest first.
    """
    top = min(probe.short_side, MAX_RUNG)
    top -= top % 2  # H.264 4:2:0 needs even dimensions
    sides = {side for side in LADDER_KBPS if side <= top}
    sides.add(top)  # the original resolution, if it is not a standard rung
    return [_rendition(probe, side) for side in sorted(sides, reverse=True)]


def hls_command(source: Path, workdir: Path, renditions: list[Rendition]) -> list[str]:
    """One ffmpeg run: decode once, split, and write one HLS output per rung."""
    count = len(renditions)
    labels = [f"v{index}" for index in range(count)]
    split = f"[0:v:0]format=yuv420p,split={count}" + "".join(f"[s{i}]" for i in range(count))
    scales = [
        f"[s{index}]scale={rendition.width}:{rendition.height}[{labels[index]}]"
        for index, rendition in enumerate(renditions)
    ]
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-threads",
        str(DECODE_THREADS),
        "-filter_threads",
        str(DECODE_THREADS),
        "-i",
        str(source),
        "-filter_complex",
        ";".join([split, *scales]),
    ]
    for index, rendition in enumerate(renditions):
        output = workdir / rendition.name
        output.mkdir(parents=True, exist_ok=True)
        command += [
            "-map",
            f"[{labels[index]}]",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-threads",
            str(ENCODER_THREADS),
            "-preset",
            "veryfast",
            "-profile:v",
            "high",
            "-b:v",
            f"{rendition.bitrate_kbps}k",
            "-maxrate",
            f"{int(rendition.bitrate_kbps * 1.1)}k",
            "-bufsize",
            f"{rendition.bitrate_kbps * 2}k",
            # A keyframe every 6 s in every rung: segments are clean 6 s cuts
            # and line up across rungs, so players can switch quality cleanly.
            "-force_key_frames",
            "expr:gte(t,n_forced*6)",
            "-c:a",
            "aac",
            "-b:a",
            f"{AUDIO_KBPS}k",
            "-ac",
            "2",
            "-f",
            "hls",
            "-hls_time",
            "6",
            "-hls_playlist_type",
            "vod",
            "-hls_segment_filename",
            str(output / "segment_%03d.ts"),
            str(output / "index.m3u8"),
        ]
    return command


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
        # At most 1280 px wide: it is shown as a card, not full screen.
        "-vf",
        "scale='min(1280,iw)':-2",
        "-q:v",
        "3",
        str(output),
    ]


def write_master_playlist(output: Path, renditions: list[Rendition]) -> None:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for rendition in renditions:
        bandwidth = (rendition.bitrate_kbps + AUDIO_KBPS) * 1000
        lines += [
            f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
            f"RESOLUTION={rendition.width}x{rendition.height}",
            f"{rendition.name}/index.m3u8",
        ]
    output.write_text("\n".join(lines) + "\n")


async def transcode_hls(
    source: Path, workdir: Path, probe: MediaProbe
) -> tuple[Path, Path, list[Rendition]]:
    renditions = ladder_for(probe)
    await _run(
        hls_command(source, workdir, renditions),
        timeout_s=transcode_timeout_s(probe.duration_s, renditions),
        rejected="The video could not be converted.",
    )
    master = workdir / "master.m3u8"
    write_master_playlist(master, renditions)
    thumbnail = workdir / "thumbnail.jpg"
    await _run(
        thumbnail_command(source, thumbnail, probe.duration_s),
        timeout_s=THUMBNAIL_TIMEOUT_S,
        rejected="A thumbnail could not be made from the video.",
    )
    return master, thumbnail, renditions
