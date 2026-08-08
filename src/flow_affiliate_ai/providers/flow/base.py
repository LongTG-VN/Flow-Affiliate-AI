from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class FlowHealth:
    healthy: bool
    chrome_reachable: bool
    logged_in: bool
    message: str


@dataclass(frozen=True)
class CostQuote:
    quoted_credit_cost: int
    max_credit_cost: int
    can_proceed: bool
    currency: str = "CREDIT"


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_text_to_video: bool = True
    supports_reference_images: bool = True
    supports_image_to_image: bool = True
    supports_exact_start_frame: bool = False
    supports_ten_second_reference_video: bool = True
    max_reference_images: int = 7


@dataclass(frozen=True)
class FlowImageGenerationRequest:
    job_id: str
    prompt: str
    reference_paths: List[str] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    model: str = "nano2"
    output_path: str = ""
    idempotency_key: str = ""


@dataclass(frozen=True)
class FlowImageResult:
    job_id: str
    status: str
    output_path: str
    error_message: Optional[str] = None


@dataclass(frozen=True)
class FlowGenerationRequest:
    job_id: str
    prompt: str
    duration_seconds: int = 10
    aspect_ratio: str = "9:16"
    max_credit_cost: int = 15
    idempotency_key: str = ""
    output_directory: str = ""
    reference_paths: List[str] = field(default_factory=list)
    start_frame_path: Optional[str] = None


@dataclass(frozen=True)
class ProviderJobRef:
    provider_job_id: str
    status: str
    quoted_credit_cost: int


@dataclass(frozen=True)
class ProviderJobStatus:
    provider_job_id: str
    status: str
    progress_pct: int = 0
    output_paths: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


class FlowProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    def health(self) -> FlowHealth: ...
    def generate_image(self, request: FlowImageGenerationRequest) -> FlowImageResult: ...
    def estimate(self, request: FlowGenerationRequest) -> CostQuote: ...
    def submit(self, request: FlowGenerationRequest) -> ProviderJobRef: ...
    def poll(self, provider_job_id: str) -> ProviderJobStatus: ...
    def download(self, provider_job_id: str, output_dir: Path) -> List[Path]: ...
    def cancel(self, provider_job_id: str) -> None: ...
