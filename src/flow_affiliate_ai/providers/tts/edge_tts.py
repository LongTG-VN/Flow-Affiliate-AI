import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from .base import TTSHealth, TTSRequest, TTSResult


class EdgeTtsProvider:
    def __init__(self, ffprobe_bin: str = "ffprobe", timeout_seconds: float = 120.0) -> None:
        self.ffprobe_bin = shutil.which(ffprobe_bin) or ffprobe_bin
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    @staticmethod
    def _edge_tts():
        try:
            import edge_tts
            return edge_tts
        except ImportError:
            return None

    def health(self) -> TTSHealth:
        if self._edge_tts() is None:
            return TTSHealth(False, "edge-tts is not installed")
        return TTSHealth(True, "Edge TTS operational")

    def _duration_ms(self, path: Path) -> int:
        result = subprocess.run(
            [self.ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return int(float(result.stdout.strip()) * 1000)

    async def _run(self, request: TTSRequest, audio: Path, boundaries: Path) -> None:
        edge_tts = self._edge_tts()
        if edge_tts is None:
            raise RuntimeError("edge-tts is not installed")
        communicate = edge_tts.Communicate(
            text=request.text,
            voice=request.voice,
            rate=request.rate,
            volume=request.volume,
            pitch=request.pitch,
        )
        rows = []
        with audio.open("wb") as handle:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    handle.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    rows.append(json.dumps(chunk, ensure_ascii=False))
        boundaries.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def synthesize(self, request: TTSRequest) -> TTSResult:
        health = self.health()
        if not health.healthy:
            return TTSResult(request.job_id, "FAILED", "", None, 0, "", health.message)

        out_dir = Path(request.output_directory)
        out_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16]
        final_audio = out_dir / f"voice_{key}.mp3"
        final_boundary = out_dir / f"boundary_{key}.jsonl"
        if final_audio.exists():
            return TTSResult(
                request.job_id, "COMPLETED", str(final_audio),
                str(final_boundary) if final_boundary.exists() else None,
                self._duration_ms(final_audio), "audio/mpeg"
            )

        tmp_audio = out_dir / f".{final_audio.name}.{uuid.uuid4().hex}.tmp"
        tmp_boundary = out_dir / f".{final_boundary.name}.{uuid.uuid4().hex}.tmp"
        try:
            asyncio.run(asyncio.wait_for(self._run(request, tmp_audio, tmp_boundary), self.timeout_seconds))
            duration = self._duration_ms(tmp_audio)
            os.replace(tmp_audio, final_audio)
            os.replace(tmp_boundary, final_boundary)
            return TTSResult(request.job_id, "COMPLETED", str(final_audio), str(final_boundary), duration, "audio/mpeg")
        except Exception as exc:
            for path in (tmp_audio, tmp_boundary):
                if path.exists():
                    path.unlink()
            return TTSResult(request.job_id, "FAILED", "", None, 0, "", str(exc))
