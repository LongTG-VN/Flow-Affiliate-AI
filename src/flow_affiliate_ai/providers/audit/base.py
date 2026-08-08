from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AuditResult:
    asset_path: str
    sha256: str
    container_metadata: dict[str, Any] = field(default_factory=dict)
    c2pa: dict[str, Any] = field(default_factory=dict)
    invisible_watermark: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitizeResult:
    output_path: str
    performed: bool
    reason: str
    source_sha256: str
    output_sha256: str
    error: str | None = None


class ProvenanceAuditor(Protocol):
    def audit(self, asset_path: str) -> AuditResult: ...
