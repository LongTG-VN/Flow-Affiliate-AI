import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from flow_affiliate_ai.providers.render.base import ClipInput, RenderManifest
from flow_affiliate_ai.providers.render.ffmpeg import FfmpegRenderProvider


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required for renderer integration test",
)


def _make_clip(path: Path, frequency: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=320x568:rate=15:duration=0.45",
            "-vf",
            f"hue=h={frequency % 360}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )


def _make_voice(path: Path, seconds: float = 0.8) -> None:
    sample_rate = 24000
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        data = bytearray()
        for index in range(frames):
            value = int(4000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            data.extend(struct.pack("<h", value))
        handle.writeframes(bytes(data))


def test_ffmpeg_renderer_outputs_vertical_h264_aac_master(tmp_path):
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    voice = tmp_path / "voice.wav"
    output = tmp_path / "final.mp4"
    _make_clip(clip_a, 20)
    _make_clip(clip_b, 80)
    _make_voice(voice)

    provider = FfmpegRenderProvider(timeout_seconds=120)
    result = provider.render(
        RenderManifest(
            job_id="render-smoke",
            clips=[
                ClipInput("character", str(clip_a)),
                ClipInput("product", str(clip_b)),
            ],
            resolution=[360, 640],
            fps=15,
            voice_track=str(voice),
            output_path=str(output),
        )
    )

    assert result.status == "COMPLETED", result.error_message
    assert output.is_file()
    media = provider.probe(output)
    assert (media.width, media.height) == (360, 640)
    assert media.video_codec == "h264"
    assert media.audio_codec == "aac"
    assert media.audio_channels == 2
    assert media.audio_sample_rate == 48000
    assert media.duration_seconds > 0.7
    assert len(media.file_sha256) == 64


def test_ffmpeg_validation_rejects_missing_inputs(tmp_path):
    provider = FfmpegRenderProvider()
    result = provider.validate_inputs(
        RenderManifest(
            job_id="invalid",
            clips=[ClipInput("missing", str(tmp_path / "missing.mp4"))],
            output_path=str(tmp_path / "final.mp4"),
        )
    )
    assert result.valid is False
    assert any("clip missing" in error for error in result.errors)
