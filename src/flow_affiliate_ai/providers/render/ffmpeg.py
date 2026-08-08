import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import List

from .base import MediaProbe, RenderJobRef, RenderManifest, ValidationResult


OVERLAY_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _concat_escape(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _filter_escape(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _tail(text: str, lines: int = 40) -> str:
    rows = text.strip().splitlines()
    return "\n".join(rows[-lines:])


def _overlay_xy(position: str, margin: int) -> tuple[str, str]:
    if position == "top-left":
        return str(margin), str(margin)
    if position == "top-right":
        return f"W-w-{margin}", str(margin)
    if position == "bottom-left":
        return str(margin), f"H-h-{margin}"
    if position == "center":
        return "(W-w)/2", "(H-h)/2"
    return f"W-w-{margin}", f"H-h-{margin}"


class FfmpegRenderProvider:
    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        timeout_seconds: int = 1800,
        music_volume: float = 0.18,
    ) -> None:
        self.ffmpeg_bin = shutil.which(ffmpeg_bin) or ffmpeg_bin
        self.ffprobe_bin = shutil.which(ffprobe_bin) or ffprobe_bin
        self.timeout_seconds = timeout_seconds
        self.music_volume = music_volume

    def validate_inputs(self, manifest: RenderManifest) -> ValidationResult:
        errors: List[str] = []
        if not manifest.clips:
            errors.append("no clips supplied")
        if len(manifest.resolution) != 2 or any(int(v) <= 0 for v in manifest.resolution):
            errors.append("resolution must contain two positive integers")
        if manifest.fps <= 0:
            errors.append("fps must be positive")
        for clip in manifest.clips:
            if not Path(clip.path).is_file():
                errors.append(f"clip missing: {clip.path}")
            if clip.trim_in_ms < 0 or clip.trim_out_ms < 0:
                errors.append(f"negative trim: {clip.clip_id}")
            if clip.trim_out_ms and clip.trim_out_ms <= clip.trim_in_ms:
                errors.append(f"invalid trim range: {clip.clip_id}")
        for label, path in (
            ("voice", manifest.voice_track),
            ("music", manifest.music_track),
            ("captions", manifest.captions_ass),
            ("overlay", manifest.overlay_image),
        ):
            if path and not Path(path).is_file():
                errors.append(f"{label} missing: {path}")
        if manifest.overlay_position not in OVERLAY_POSITIONS:
            errors.append(f"invalid overlay position: {manifest.overlay_position}")
        if not 1 <= float(manifest.overlay_width_pct) <= 50:
            errors.append("overlay width must be between 1 and 50 percent")
        if manifest.overlay_margin_px < 0:
            errors.append("overlay margin cannot be negative")
        return ValidationResult(not errors, errors)

    def probe(self, output: Path) -> MediaProbe:
        result = subprocess.run(
            [self.ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(output)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        data = json.loads(result.stdout)
        width = height = channels = sample_rate = 0
        video_codec = audio_codec = ""
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                width = int(stream.get("width", 0))
                height = int(stream.get("height", 0))
                video_codec = stream.get("codec_name", "")
            elif stream.get("codec_type") == "audio":
                audio_codec = stream.get("codec_name", "")
                channels = int(stream.get("channels", 0) or 0)
                sample_rate = int(stream.get("sample_rate", 0) or 0)
        return MediaProbe(
            width=width,
            height=height,
            duration_seconds=float(data.get("format", {}).get("duration", 0.0)),
            video_codec=video_codec,
            audio_codec=audio_codec,
            file_sha256=_sha256(output),
            audio_channels=channels,
            audio_sample_rate=sample_rate,
        )

    def _effective_video_duration(self, manifest: RenderManifest) -> float:
        total = 0.0
        for clip in manifest.clips:
            media = self.probe(Path(clip.path))
            source_duration = max(0.0, media.duration_seconds)
            trim_in = clip.trim_in_ms / 1000.0
            trim_out = (
                clip.trim_out_ms / 1000.0
                if clip.trim_out_ms
                else source_duration
            )
            total += max(0.0, min(source_duration, trim_out) - trim_in)
        if total <= 0:
            raise RuntimeError("render clips have zero effective duration")
        return total

    def render(self, manifest: RenderManifest) -> RenderJobRef:
        validation = self.validate_inputs(manifest)
        if not validation.valid:
            return RenderJobRef(manifest.job_id, "FAILED", "", "", "; ".join(validation.errors))

        output = Path(manifest.output_path or f"data/renders/{manifest.job_id}.mp4").resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        concat = output.parent / f".{manifest.job_id}.concat.txt"
        lines = []
        for clip in manifest.clips:
            lines.append(f"file '{_concat_escape(Path(clip.path))}'")
            if clip.trim_in_ms:
                lines.append(f"inpoint {clip.trim_in_ms / 1000:.3f}")
            if clip.trim_out_ms:
                lines.append(f"outpoint {clip.trim_out_ms / 1000:.3f}")
        concat.write_text("\n".join(lines) + "\n", encoding="utf-8")

        temp = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.mp4")
        log_path = output.parent / f".{manifest.job_id}.ffmpeg.log"
        try:
            video_duration = self._effective_video_duration(manifest)
        except Exception as exc:
            return RenderJobRef(manifest.job_id, "FAILED", "", "", str(exc))

        # Keep every audio branch finite. An unbounded `apad` can keep FFmpeg 6.x
        # buffering after the concat video ends and eventually surface ENOSPC even
        # though the output file itself is tiny. One second of headroom ensures the
        # video remains the stream that decides `-shortest`.
        audio_target = video_duration + 1.0
        cmd = [self.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(concat)]
        next_index = 1
        voice_index = None
        music_index = None
        silence_index = None
        overlay_index = None

        if manifest.voice_track:
            voice_index = next_index
            next_index += 1
            cmd += ["-i", str(Path(manifest.voice_track).resolve())]
        if manifest.music_track:
            music_index = next_index
            next_index += 1
            cmd += ["-stream_loop", "-1", "-i", str(Path(manifest.music_track).resolve())]
        if voice_index is None and music_index is None:
            silence_index = next_index
            next_index += 1
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        if manifest.overlay_image:
            overlay_index = next_index
            cmd += [
                "-loop", "1",
                "-framerate", str(manifest.fps),
                "-i", str(Path(manifest.overlay_image).resolve()),
            ]

        width, height = map(int, manifest.resolution)
        video_filters = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
            "setsar=1",
            f"fps={manifest.fps}",
        ]
        if manifest.captions_ass:
            video_filters.append(f"ass=filename='{_filter_escape(Path(manifest.captions_ass))}'")

        filters: list[str] = []
        base_label = "vbase" if overlay_index is not None else "vout"
        filters.append(f"[0:v]{','.join(video_filters)}[{base_label}]")
        if overlay_index is not None:
            overlay_width = max(1, round(width * float(manifest.overlay_width_pct) / 100.0))
            x, y = _overlay_xy(manifest.overlay_position, manifest.overlay_margin_px)
            filters += [
                f"[{overlay_index}:v]scale={overlay_width}:-1,format=rgba[overlayimg]",
                f"[vbase][overlayimg]overlay=x={x}:y={y}:shortest=1:format=auto[vout]",
            ]

        finite_audio = f"apad=whole_dur={audio_target:.3f},atrim=duration={audio_target:.3f}"
        finite_stream = f"atrim=duration={audio_target:.3f}"
        if voice_index is not None and music_index is not None:
            filters += [
                f"[{voice_index}:a]aresample=48000,{finite_audio}[voice]",
                f"[{music_index}:a]aresample=48000,volume={self.music_volume},{finite_stream}[music]",
                "[voice][music]amix=inputs=2:duration=longest:dropout_transition=2,alimiter=limit=0.95[aout]",
            ]
        elif voice_index is not None:
            filters.append(
                f"[{voice_index}:a]aresample=48000,{finite_audio},alimiter=limit=0.95[aout]"
            )
        elif music_index is not None:
            filters.append(
                f"[{music_index}:a]aresample=48000,volume={self.music_volume},{finite_stream},alimiter=limit=0.95[aout]"
            )
        else:
            filters.append(
                f"[{silence_index}:a]{finite_stream},alimiter=limit=0.95[aout]"
            )

        codec = {"h264": "libx264", "h265": "libx265", "hevc": "libx265"}.get(
            manifest.video_codec.lower(), manifest.video_codec
        )
        cmd += [
            "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", codec, "-pix_fmt", "yuv420p",
            "-c:a", manifest.audio_codec, "-ac", "2", "-ar", "48000", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(temp),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            log_path.write_text(
                "COMMAND\n"
                + " ".join(cmd)
                + "\n\nSTDOUT\n"
                + (result.stdout or "")
                + "\n\nSTDERR\n"
                + (result.stderr or ""),
                encoding="utf-8",
            )
            if result.returncode != 0:
                detail = _tail(result.stderr or result.stdout or "ffmpeg failed")
                raise RuntimeError(
                    f"ffmpeg failed with exit code {result.returncode}:\n{detail}"
                )
            media = self.probe(temp)
            if not media.audio_codec:
                raise RuntimeError("rendered master has no audio")
            os.replace(temp, output)
            return RenderJobRef(manifest.job_id, "COMPLETED", str(output), _sha256(output))
        except Exception as exc:
            if temp.exists():
                temp.unlink()
            return RenderJobRef(manifest.job_id, "FAILED", "", "", str(exc))
