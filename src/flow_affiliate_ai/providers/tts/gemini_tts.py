import base64
import hashlib
import os
import uuid
import wave
from pathlib import Path
from typing import Any, Optional

from .base import TTSHealth, TTSRequest, TTSResult

DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_VOICE = "Zephyr"
SAMPLE_RATE = 24000


class GeminiTtsProvider:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_TTS_MODEL", DEFAULT_MODEL)

    def _genai(self) -> Optional[Any]:
        try:
            from google import genai
            return genai
        except ImportError:
            return None

    def health(self) -> TTSHealth:
        if not self.api_key:
            return TTSHealth(False, "GEMINI_API_KEY is not configured")
        if self._genai() is None:
            return TTSHealth(False, "google-genai is not installed")
        return TTSHealth(True, "Gemini TTS operational")

    @staticmethod
    def _prompt(request: TTSRequest) -> str:
        return (
            "Đọc nguyên văn phần LỜI THOẠI bằng tiếng Việt. "
            "Giọng nữ trẻ, tự nhiên, thân thiện như creator TikTok thời trang; "
            "rõ dấu, nhịp vừa, không đọc kiểu quảng cáo quá lố. "
            "Không thêm hoặc lược bỏ từ.\n\n"
            f"LỜI THOẠI:\n{request.text.strip()}"
        )

    @staticmethod
    def _decode(data: Any) -> bytes:
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return base64.b64decode(data, validate=True)
        raise ValueError("unsupported Gemini audio payload")

    @staticmethod
    def _write_wav(path: Path, pcm: bytes) -> int:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(pcm)
        return round((len(pcm) / 2) * 1000 / SAMPLE_RATE)

    def synthesize(self, request: TTSRequest) -> TTSResult:
        health = self.health()
        if not health.healthy:
            return TTSResult(request.job_id, "FAILED", "", None, 0, "", health.message)

        out_dir = Path(request.output_directory)
        out_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16]
        final = out_dir / f"voice_{key}.wav"
        if final.exists():
            with wave.open(str(final), "rb") as handle:
                duration = round(handle.getnframes() * 1000 / handle.getframerate())
            return TTSResult(request.job_id, "COMPLETED", str(final), None, duration, "audio/wav")

        tmp = out_dir / f".{final.name}.{uuid.uuid4().hex}.tmp.wav"
        client = None
        try:
            genai = self._genai()
            client = genai.Client(api_key=self.api_key)
            response = client.interactions.create(
                model=self.model,
                input=self._prompt(request),
                response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": request.voice or DEFAULT_VOICE}]},
            )
            pcm = self._decode(response.output_audio.data)
            duration = self._write_wav(tmp, pcm)
            os.replace(tmp, final)
            return TTSResult(request.job_id, "COMPLETED", str(final), None, duration, "audio/wav")
        except Exception as exc:
            if tmp.exists():
                tmp.unlink()
            return TTSResult(request.job_id, "FAILED", "", None, 0, "", str(exc))
        finally:
            if client is not None:
                client.close()
