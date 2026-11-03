import json

import pytest

from app.worker import ffmpeg


def test_ladder_skips_720p_for_small_sources() -> None:
    assert [item.name for item in ffmpeg.renditions_for_height(360)] == ["360p"]
    assert [item.name for item in ffmpeg.renditions_for_height(1080)] == ["360p", "720p"]


def test_hls_command_uses_six_second_vod_segments(tmp_path) -> None:
    command = ffmpeg.hls_command(
        tmp_path / "original.mp4",
        tmp_path / "360p" / "index.m3u8",
        ffmpeg.Rendition("360p", 360, 800),
    )
    assert "-hls_time" in command
    assert command[command.index("-hls_time") + 1] == "6"
    assert command[command.index("-hls_playlist_type") + 1] == "vod"


def test_master_playlist_contains_each_rendition(tmp_path) -> None:
    output = tmp_path / "master.m3u8"
    ffmpeg.write_master_playlist(
        output,
        ffmpeg.MediaProbe(duration_s=3, width=1280, height=720),
        ffmpeg.renditions_for_height(720),
    )
    body = output.read_text()
    assert "360p/index.m3u8" in body
    assert "720p/index.m3u8" in body
    assert "RESOLUTION=640x360" in body


async def test_probe_rejects_a_file_without_a_video_stream(monkeypatch, tmp_path) -> None:
    async def fake_run(*_: object, **__: object) -> tuple[bytes, bytes]:
        return json.dumps({"streams": [], "format": {"duration": "3"}}).encode(), b""

    monkeypatch.setattr(ffmpeg, "_run", fake_run)
    with pytest.raises(ffmpeg.PermanentTranscodeError, match="video stream"):
        await ffmpeg.probe_media(tmp_path / "junk.mp4")
