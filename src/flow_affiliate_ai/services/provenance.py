import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flow_affiliate_ai.jobs import JobStore
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


class ProvenancePipelineWrapper:
    """Run provenance audit automatically after the existing affiliate pipeline.

    The wrapper delegates all normal pipeline attributes (flow, voice, render, etc.)
    so the web health endpoint and existing callers keep working unchanged.
    """

    def __init__(
        self,
        *,
        pipeline: Any,
        provenance: ProvenanceService,
        data_root: Path,
    ) -> None:
        self.pipeline = pipeline
        self.provenance = provenance
        self.data_root = data_root.resolve()
        self.job_store: JobStore = pipeline.job_store

    def __getattr__(self, name: str) -> Any:
        return getattr(self.pipeline, name)

    def _finalize_provenance(self, job_id: str) -> dict:
        state = self.job_store.load(job_id)
        if state is None:
            raise RuntimeError(f"job state missing after pipeline run: {job_id}")
        if not state.final_video or not Path(state.final_video).is_file():
            raise RuntimeError("final rendered video is missing before provenance audit")

        if (
            state.provenance_report
            and state.publish_video
            and Path(state.provenance_report).is_file()
            and Path(state.publish_video).is_file()
        ):
            state.final_video = state.publish_video
            state.status = "COMPLETED"
            self.job_store.clear_error(state)
            return asdict(state)

        rendered_master = state.metadata.get("rendered_master") or state.final_video
        rendered_master_path = Path(rendered_master).resolve()
        if not rendered_master_path.is_file():
            rendered_master_path = Path(state.final_video).resolve()

        render_dir = self.data_root / "runs" / job_id / "renders"
        report_path = render_dir / "provenance_report.json"
        publish_path = render_dir / "final_publish.mp4"

        state.status = "AUDITING"
        self.job_store.clear_error(state)
        try:
            payload = self.provenance.process(
                source_path=str(rendered_master_path),
                report_path=str(report_path),
                publish_path=str(publish_path),
            )
        except Exception as exc:
            self.job_store.mark_error(state, "AUDITING", str(exc))
            raise

        state.metadata["rendered_master"] = str(rendered_master_path)
        state.metadata["c2pa_status"] = str(
            payload.get("source", {}).get("c2pa", {}).get("status", "unknown")
        )
        state.metadata["invisible_watermark_status"] = str(
            payload.get("source", {}).get("invisible_watermark", {}).get("status", "unknown")
        )
        state.metadata["privacy_metadata_sanitized"] = str(
            bool(payload.get("privacy_sanitization", {}).get("performed", False))
        ).lower()
        state.provenance_report = str(report_path.resolve())
        state.publish_video = str(publish_path.resolve())
        # Keep the existing web/API contract: `final_video` now points at the
        # publish-ready copy, while the untouched rendered master stays in metadata.
        state.final_video = state.publish_video
        state.status = "COMPLETED"
        self.job_store.clear_error(state)
        return asdict(state)

    def run(self, *args: Any, **kwargs: Any) -> dict:
        result = self.pipeline.run(*args, **kwargs)
        job_id = kwargs.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return result
        return self._finalize_provenance(job_id)
