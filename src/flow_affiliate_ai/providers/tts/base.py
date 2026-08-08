from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class TTSHealth:
    healthy: bool
    message: str


@dataclass(frozen=True)
class TTSRequest:
    job_id: str
    text: str
    voice: str
    output_directory: str
    idempotency_key: str
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"


@dataclass(frozen=True)
class TTSResult:
    job_id: str
    status: str
    audio_path: str
    boundary_path: Optional[str]
    duration_ms: int
    mime_type: str
    error_message: Optional[str] = None


class TTSProvider(Protocol):
    def health(self) -> TTSHealth: ...
    def synthesize(self, request: TTSRequest) -> TTSResult: ...
