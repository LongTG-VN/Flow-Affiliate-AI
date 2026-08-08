import json
import os
from dataclasses import asdict
from pathlib import Path

from flow_affiliate_ai.providers.audit.base import ProvenanceAuditor
from flow_affiliate_ai.providers.audit.local import PrivacyMetadataSanitizer


class ProvenanceService:
    """Audit rendered media and prepare a publish copy without defeating provenance."""

    def __init__(
        self,
        auditor: ProvenanceAuditor,
        sanitizer: PrivacyMetadataSanitizer,
    ) -> None:
        self.auditor = auditor
        self.sanitizer = sanitizer

    def process(
        self,
        *,
        source_path: str,
        report_path: str,
        publish_path: str,
    ) -> dict:
        source = Path(source_path).expanduser().resolve()
        report = Path(report_path).expanduser().resolve()
        publish = Path(publish_path).expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        publish.parent.mkdir(parents=True, exist_ok=True)

        before = self.auditor.audit(str(source))
        c2pa_status = str(before.c2pa.get("status", "unknown"))
        sanitize = self.sanitizer.sanitize(
            source_path=str(source),
            output_path=str(publish),
            c2pa_status=c2pa_status,
        )
        after = self.auditor.audit(str(publish))

        payload = {
            "schema_version": "1.0",
            "source": asdict(before),
            "privacy_sanitization": asdict(sanitize),
            "publish": asdict(after),
            "policy": {
                "c2pa_behavior": (
                    "read-only audit; privacy metadata sanitization is skipped when "
                    "C2PA is present or unknown"
                ),
                "invisible_watermark_behavior": (
                    "report detector availability/status only; no watermark removal or defeat"
                ),
            },
        }
        temp = report.with_suffix(report.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, report)
        return payload
