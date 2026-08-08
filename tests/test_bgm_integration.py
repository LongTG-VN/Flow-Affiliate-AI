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
    reason="ffmpeg/ffprobe are required for BGM integration test",
)


def _make_clip(path: Path, hue: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x568:rate=15:duration=0.55",
            "-vf",
            f"hue=h={hue}",
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


def _make_wave(path: Path, *, frequency: int, seconds: float, sample_rate: int) -> None:
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        data = bytearray()
        for index in range(frames):
            value = int(3500 * math.sin(2 * math.pi * frequency * index / sample_rate))
            data.extend(struct.pack("<h", value))
        handle.writeframes(bytes(data))


def test_ffmpeg_renderer_mixes_voice_and_looped_bgm(tmp_path: Path):
    clip_a = tmp_path / "character.mp4"
    clip_b = tmp_path / "product.mp4"
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    output = tmp_path / "final-with-bgm.mp4"

    _make_clip(clip_a, 20)
    _make_clip(clip_b, 100)
    _make_wave(voice, frequency=440, seconds=0.9, sample_rate=24000)
    _make_wave(music, frequency=220, seconds=0.2, sample_rate=48000)

    provider = FfmpegRenderProvider(timeout_seconds=120, music_volume=0.18)
    result = provider.render(
        RenderManifest(
            job_id="render-bgm",
            clips=[
                ClipInput("character", str(clip_a)),
                ClipInput("product", str(clip_b)),
            ],
            resolution=[360, 640],
            fps=15,
            voice_track=str(voice),
            music_track=str(music),
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
    assert media.duration_seconds > 0.5
