import json

import pytest

from app.worker import ffmpeg
from app.worker.ffmpeg import MediaProbe


def _names(width: int, height: int) -> list[str]:
    return [r.name for r in ffmpeg.ladder_for(MediaProbe(10, width, height))]


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1920, 1080, ["1080p", "720p", "480p", "360p", "240p", "144p"]),
        (1280, 720, ["720p", "480p", "360p", "240p", "144p"]),
        (640, 360, ["360p", "240p", "144p"]),
        # Not a standard rung: the original resolution is kept on top.
        (1600, 900, ["900p", "720p", "480p", "360p", "240p", "144p"]),
        # Portrait phone video: named by the short side.
        (1080, 1920, ["1080p", "720p", "480p", "360p", "240p", "144p"]),
        # Above 4K: capped at 2160p.
        (7680, 4320, ["2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"]),
        # Smaller than every rung: only the original.
        (176, 99, ["98p"]),
    ],
)
def test_ladder_has_every_rung_up_to_the_source_plus_the_original(
    width: int, height: int, expected: list[str]
) -> None:
    assert _names(width, height) == expected


def test_rungs_keep_the_aspect_ratio_with_even_dimensions() -> None:
    landscape = {r.name: (r.width, r.height) for r in ffmpeg.ladder_for(MediaProbe(1, 1920, 1080))}
    assert landscape["1080p"] == (1920, 1080)
    assert landscape["720p"] == (1280, 720)
    assert landscape["144p"] == (256, 144)
    portrait = {r.name: (r.width, r.height) for r in ffmpeg.ladder_for(MediaProbe(1, 1080, 1920))}
    assert portrait["720p"] == (720, 1280)
    for rendition in ffmpeg.ladder_for(MediaProbe(1, 1001, 563)):
        assert rendition.width % 2 == 0 and rendition.height % 2 == 0


def test_bitrates_rise_with_resolution() -> None:
    ladder = ffmpeg.ladder_for(MediaProbe(1, 1600, 900))
    bitrates = [r.bitrate_kbps for r in ladder]
    assert bitrates == sorted(bitrates, reverse=True)
    # 900p sits between the 720p and 1080p table entries.
    assert ffmpeg.LADDER_KBPS[720] < ladder[0].bitrate_kbps < ffmpeg.LADDER_KBPS[1080]


def test_one_ffmpeg_run_decodes_once_and_writes_every_rung(tmp_path) -> None:
    ladder = ffmpeg.ladder_for(MediaProbe(10, 1280, 720))
    command = ffmpeg.hls_command(tmp_path / "original", tmp_path / "hls", ladder)

    assert command.count("-i") == 1
    # Fixed thread counts: ffmpeg's defaults follow the host's CPUs, not the container's.
    assert command[command.index("-i") - 4 : command.index("-i")] == [
        "-threads",
        "2",
        "-filter_threads",
        "2",
    ]
    assert command.count("libx264") == len(ladder)
    assert all(
        command[i + 1 : i + 3] == ["-threads", "1"]
        for i, arg in enumerate(command)
        if arg == "libx264"
    )
    graph = command[command.index("-filter_complex") + 1]
    assert graph.startswith(f"[0:v:0]format=yuv420p,split={len(ladder)}")
    assert "scale=1280:720" in graph and "scale=256:144" in graph
    # One HLS output per rung, each with 6 s segments and aligned keyframes.
    assert command.count("-f") == len(ladder)
    assert command.count("expr:gte(t,n_forced*6)") == len(ladder)
    assert command[command.index("-hls_time") + 1] == "6"
    for rendition in ladder:
        assert str(tmp_path / "hls" / rendition.name / "index.m3u8") in command


def test_master_playlist_lists_every_rung_highest_first(tmp_path) -> None:
    output = tmp_path / "master.m3u8"
    ladder = ffmpeg.ladder_for(MediaProbe(3, 1920, 1080))
    ffmpeg.write_master_playlist(output, ladder)
    body = output.read_text()
    assert body.index("1080p/index.m3u8") < body.index("144p/index.m3u8")
    assert "RESOLUTION=1920x1080" in body
    assert "RESOLUTION=256x144" in body


def test_timeout_scales_with_length_and_pixels_above_a_floor() -> None:
    def minutes(width: int, height: int, duration_s: float) -> float:
        ladder = ffmpeg.ladder_for(MediaProbe(duration_s, width, height))
        return ffmpeg.transcode_timeout_s(duration_s, ladder) / 60

    assert minutes(1920, 1080, 3) == ffmpeg.MIN_TIMEOUT_S / 60
    assert minutes(640, 360, 600) == ffmpeg.MIN_TIMEOUT_S / 60  # small ladder: floor
    assert 30 < minutes(1920, 1080, 600) < 40
    assert 120 < minutes(3840, 2160, 600) < 180
    assert minutes(3840, 2160, 600) > 4 * minutes(1920, 1080, 600)


async def test_probe_rejects_a_file_without_a_video_stream(monkeypatch, tmp_path) -> None:
    async def fake_run(*_: object, **__: object) -> tuple[bytes, bytes]:
        return json.dumps({"streams": [], "format": {"duration": "3"}}).encode(), b""

    monkeypatch.setattr(ffmpeg, "_run", fake_run)
    with pytest.raises(ffmpeg.PermanentTranscodeError, match="video stream"):
        await ffmpeg.probe_media(tmp_path / "junk.mp4")


async def test_probe_swaps_dimensions_for_rotated_phone_video(monkeypatch, tmp_path) -> None:
    stream = {
        "codec_type": "video",
        "width": 1920,
        "height": 1080,
        "side_data_list": [{"rotation": -90}],
    }

    async def fake_run(*_: object, **__: object) -> tuple[bytes, bytes]:
        return json.dumps({"streams": [stream], "format": {"duration": "3"}}).encode(), b""

    monkeypatch.setattr(ffmpeg, "_run", fake_run)
    probe = await ffmpeg.probe_media(tmp_path / "phone.mp4")
    assert (probe.width, probe.height) == (1080, 1920)


async def test_a_rejected_source_gets_a_short_reason_and_keeps_the_detail() -> None:
    with pytest.raises(ffmpeg.PermanentTranscodeError) as caught:
        await ffmpeg._run(
            ["sh", "-c", "echo '/tmp/videio-1-x/original: Invalid data' >&2; exit 1"],
            timeout_s=10,
            rejected="The file could not be read as a video.",
        )
    assert str(caught.value) == "The file could not be read as a video."
    assert "/tmp/videio-1-x/original" in caught.value.detail


async def test_a_timeout_fails_for_good_instead_of_retrying() -> None:
    with pytest.raises(ffmpeg.PermanentTranscodeError, match="too long") as caught:
        await ffmpeg._run(["sleep", "5"], timeout_s=0.2, rejected="unused")
    assert "timed out" in caught.value.detail


async def test_a_killed_process_is_retried_not_blamed_on_the_file() -> None:
    with pytest.raises(ffmpeg.RetryableTranscodeError, match="signal 9"):
        await ffmpeg._run(["sh", "-c", "kill -9 $$"], timeout_s=10, rejected="unused")
