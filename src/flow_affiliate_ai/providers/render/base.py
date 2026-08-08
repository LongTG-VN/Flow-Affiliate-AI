from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class ClipInput:
    clip_id: str
    path: str
    trim_in_ms: int = 0
    trim_out_ms: int = 0


@dataclass(frozen=True)
class RenderManifest:
    job_id: str
    clips: List[ClipInput] = field(default_factory=list)
    resolution: List[int] = field(default_factory=lambda: [1080, 1920])
    fps: int = 30
    video_codec: str = "h264"
    audio_codec: str = "aac"
    voice_track: Optional[str] = None
    music_track: Optional[str] = None
    captions_ass: Optional[str] = None
    output_path: str = ""


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MediaProbe:
    width: int
    height: int
    duration_seconds: float
    video_codec: str
    audio_codec: str
    file_sha256: str
    audio_channels: int = 0
    audio_sample_rate: int = 0


@dataclass(frozen=True)
class RenderJobRef:
    job_id: str
    status: str
    master_output_path: str
    sha256: str
    error_message: Optional[str] = None


class RenderProvider(Protocol):
    def validate_inputs(self, manifest: RenderManifest) -> ValidationResult: ...
    def render(self, manifest: RenderManifest) -> RenderJobRef: ...
    def probe(self, output: Path) -> MediaProbe: ...
