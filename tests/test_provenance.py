import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from flow_affiliate_ai.jobs import AffiliateJobState, JobStore
from flow_affiliate_ai.providers.audit.base import AuditResult
from flow_affiliate_ai.providers.audit.local import (
    LocalProvenanceAuditor,
    PrivacyMetadataSanitizer,
)
from flow_affiliate_ai.services.provenance import (
    ProvenancePipelineWrapper,
    ProvenanceService,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_c2pa_summary_distinguishes_present_and_absent():
    present = LocalProvenanceAuditor._c2pa_summary(
        {
            "active_manifest": "urn:c2pa:test",
            "manifests": {"urn:c2pa:test": {"claim_generator": "test"}},
            "validation_status": [],
        }
    )
    absent = LocalProvenanceAuditor._c2pa_summary({})

    assert present["status"] == "present"
    assert present["manifest_count"] == 1
    assert absent["status"] == "absent"
    assert absent["manifest_count"] == 0


class _UnknownAuditor:
    def audit(self, asset_path: str) -> AuditResult:
        path = Path(asset_path)
        return AuditResult(
            asset_path=str(path.resolve()),
            sha256=_hash(path),
            container_metadata={"available": True, "format": {"tags": {"title": "private"}}},
            c2pa={"available": False, "status": "unknown"},
            invisible_watermark={"status": "unknown", "detector_available": False},
        )


def test_provenance_service_preserves_source_bytes_when_c2pa_unknown(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-a-real-video-but-copy-path-is-enough")
    publish = tmp_path / "publish.mp4"
    report = tmp_path / "provenance_report.json"

    service = ProvenanceService(
        auditor=_UnknownAuditor(),
        sanitizer=PrivacyMetadataSanitizer(ffmpeg_bin="ffmpeg"),
    )
    payload = service.process(
        source_path=str(source),
        report_path=str(report),
        publish_path=str(publish),
    )

    assert publish.read_bytes() == source.read_bytes()
    assert payload["privacy_sanitization"]["performed"] is False
    assert payload["source"]["c2pa"]["status"] == "unknown"
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["policy"]["c2pa_behavior"].startswith("read-only audit")


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required for metadata integration test",
)
def test_privacy_sanitizer_strips_ordinary_metadata_when_c2pa_absent(tmp_path: Path):
    source = tmp_path / "source.mp4"
    publish = tmp_path / "publish.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x284:r=10:d=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-shortest",
            "-metadata",
            "title=PrivateTitle",
            "-metadata",
            "comment=PrivateComment",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    auditor = LocalProvenanceAuditor(c2patool_bin="definitely-not-installed-c2patool")
    before = auditor.audit(str(source))
    before_tags = before.container_metadata.get("format", {}).get("tags", {})
    assert before_tags.get("title") == "PrivateTitle"
    assert before_tags.get("comment") == "PrivateComment"

    result = PrivacyMetadataSanitizer().sanitize(
        source_path=str(source),
        output_path=str(publish),
        c2pa_status="absent",
    )
    after = auditor.audit(str(publish))
    after_tags = after.container_metadata.get("format", {}).get("tags", {})

    assert result.performed is True
    assert publish.is_file()
    assert "title" not in after_tags
    assert "comment" not in after_tags


class _FakeCorePipeline:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.job_store = JobStore(data_root / "jobs")

    def run(self, *args, **kwargs):
        job_id = kwargs["job_id"]
        render_dir = self.data_root / "runs" / job_id / "renders"
        render_dir.mkdir(parents=True, exist_ok=True)
        master = render_dir / "final_video.mp4"
        master.write_bytes(b"rendered-master")
        state = AffiliateJobState(
            job_id=job_id,
            character_image=str(self.data_root / "character.png"),
            product_image=str(self.data_root / "product.png"),
            final_video=str(master),
            status="COMPLETED",
        )
        self.job_store.save(state)
        return {"job_id": job_id, "final_video": str(master)}


class _FakeProvenanceService:
    def process(self, *, source_path: str, report_path: str, publish_path: str):
        source = Path(source_path)
        report = Path(report_path)
        publish = Path(publish_path)
        publish.write_bytes(source.read_bytes())
        payload = {
            "source": {
                "c2pa": {"status": "unknown"},
                "invisible_watermark": {"status": "unknown"},
            },
            "privacy_sanitization": {"performed": False},
        }
        report.write_text(json.dumps(payload), encoding="utf-8")
        return payload


def test_pipeline_wrapper_promotes_publish_copy_to_final_asset(tmp_path: Path):
    core = _FakeCorePipeline(tmp_path)
    wrapper = ProvenancePipelineWrapper(
        pipeline=core,
        provenance=_FakeProvenanceService(),
        data_root=tmp_path,
    )

    result = wrapper.run(job_id="job-audit-1")
    state = core.job_store.load("job-audit-1")

    assert state is not None
    assert state.publish_video is not None
    assert state.provenance_report is not None
    assert state.final_video == state.publish_video
    assert Path(state.final_video).read_bytes() == b"rendered-master"
    assert Path(state.provenance_report).is_file()
    assert state.metadata["rendered_master"].endswith("final_video.mp4")
    assert result["final_video"] == state.publish_video
