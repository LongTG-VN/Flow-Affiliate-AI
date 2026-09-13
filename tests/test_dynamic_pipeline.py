import json
from pathlib import Path

import pytest

from flow_affiliate_ai.dynamic_pipeline import (
    DynamicAffiliatePipeline,
    PaidShotRetryApprovalRequired,
)
from flow_affiliate_ai.providers.flow.base import (
    CostQuote,
    FlowHealth,
    FlowImageResult,
    ProviderCapabilities,
    ProviderJobRef,
    ProviderJobStatus,
)
from flow_affiliate_ai.providers.render.base import RenderJobRef, ValidationResult
from flow_affiliate_ai.services.core import FlowService, RenderService
from flow_affiliate_ai.shot_plan import ShotPlanV1


class FakePlanner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, *, model_image: Path, isolated_product_image: Path):
        self.calls += 1
        payload = {
            "schema_version": "1.0",
            "project_type": "fashion_affiliate",
            "creative_summary": "dynamic test plan",
            "wear_model_prompt": "Put the exact product on the same model.",
            "total_shots": 4,
            "shots": [
                {
                    "shot_id": "shot_01",
                    "purpose": "hook",
                    "source_type": "worn_model",
                    "duration_seconds": 4,
                    "flow_prompt": "HOOK PROMPT",
                },
                {
                    "shot_id": "shot_02",
                    "purpose": "showcase",
                    "source_type": "worn_model",
                    "duration_seconds": 6,
                    "flow_prompt": "SHOWCASE PROMPT",
                },
                {
                    "shot_id": "shot_03",
                    "purpose": "detail",
                    "source_type": "isolated_product",
                    "duration_seconds": 4,
                    "flow_prompt": "DETAIL PROMPT",
                },
                {
                    "shot_id": "shot_04",
                    "purpose": "ending",
                    "source_type": "worn_model",
                    "duration_seconds": 4,
                    "flow_prompt": "ENDING PROMPT",
                },
            ],
            "edit_sequence": ["shot_02", "shot_01", "shot_03", "shot_04"],
        }
        return ShotPlanV1.from_dict(payload), json.dumps(payload)


class FakeFlowProvider:
    COSTS = {4: 6, 6: 9, 8: 12, 10: 15}

    def __init__(self, fail_once_shot: str | None = None) -> None:
        self.outputs = {}
        self.image_prompts = []
        self.video_requests = []
        self.fail_once_shot = fail_once_shot
        self.failed = set()

    def capabilities(self):
        return ProviderCapabilities()

    def health(self):
        return FlowHealth(True, True, True, "ok")

    def generate_image(self, request):
        self.image_prompts.append(request.prompt)
        path = Path(request.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-image")
        self.outputs[request.job_id] = [str(path.resolve())]
        return FlowImageResult(request.job_id, "COMPLETED", str(path.resolve()))

    def estimate(self, request):
        cost = self.COSTS[request.duration_seconds]
        return CostQuote(cost, request.max_credit_cost, cost <= request.max_credit_cost)

    def submit(self, request):
        self.video_requests.append(request)
        if (
            self.fail_once_shot
            and self.fail_once_shot in request.job_id
            and self.fail_once_shot not in self.failed
        ):
            self.failed.add(self.fail_once_shot)
            raise RuntimeError("simulated paid Flow failure")
        out = Path(request.output_directory) / f"{request.job_id}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-video")
        self.outputs[request.job_id] = [str(out.resolve())]
        return ProviderJobRef(
            request.job_id,
            "COMPLETED",
            self.COSTS[request.duration_seconds],
        )

    def poll(self, provider_job_id):
        return ProviderJobStatus(
            provider_job_id,
            "COMPLETED",
            100,
            self.outputs.get(provider_job_id, []),
        )

    def download(self, provider_job_id, output_dir):
        return [Path(p) for p in self.outputs[provider_job_id]]

    def cancel(self, provider_job_id):
        raise RuntimeError("not supported")


class FakeRenderProvider:
    def __init__(self) -> None:
        self.manifests = []

    def validate_inputs(self, manifest):
        return ValidationResult(True, [])

    def render(self, manifest):
        self.manifests.append(manifest)
        path = Path(manifest.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-final")
        return RenderJobRef(
            job_id=manifest.job_id,
            status="COMPLETED",
            master_output_path=str(path.resolve()),
            sha256="fake",
        )

    def probe(self, output):
        raise NotImplementedError


def _inputs(tmp_path: Path):
    model = tmp_path / "model.png"
    product = tmp_path / "product.png"
    logo = tmp_path / "logo.png"
    model.write_bytes(b"model")
    product.write_bytes(b"product")
    logo.write_bytes(b"logo")
    return model, product, logo


def _pipeline(tmp_path: Path, *, fail_once_shot: str | None = None):
    flow_provider = FakeFlowProvider(fail_once_shot=fail_once_shot)
    planner = FakePlanner()
    render_provider = FakeRenderProvider()
    pipeline = DynamicAffiliatePipeline(
        flow=FlowService(flow_provider),
        planner=planner,
        render=RenderService(render_provider),
        data_root=tmp_path / "data",
    )
    return pipeline, flow_provider, planner, render_provider


def test_plan_only_generates_no_paid_video_and_reports_estimate(tmp_path):
    model, product, logo = _inputs(tmp_path)
    pipeline, flow, planner, _ = _pipeline(tmp_path)

    result = pipeline.run(
        job_id="factory-plan",
        model_image=str(model),
        product_image=str(product),
        logo_image=str(logo),
    )

    assert result["status"] == "PLAN_READY"
    assert result["metadata"]["estimated_total_credits"] == 27
    assert result["metadata"]["total_shots"] == 4
    assert planner.calls == 1
    assert len(flow.image_prompts) == 2
    assert flow.video_requests == []
    assert Path(result["isolated_product_image"]).is_file()
    assert Path(result["canonical_worn_image"]).is_file()


def test_approved_run_reuses_plan_and_renders_all_shots_in_edit_order(tmp_path):
    model, product, logo = _inputs(tmp_path)
    pipeline, flow, planner, renderer = _pipeline(tmp_path)

    planned = pipeline.run(
        job_id="factory-render",
        model_image=str(model),
        product_image=str(product),
        logo_image=str(logo),
    )
    result = pipeline.run(
        job_id="factory-render",
        model_image=str(model),
        product_image=str(product),
        logo_image=str(logo),
        approve_video_credits=True,
    )

    assert planned["status"] == "PLAN_READY"
    assert result["status"] == "COMPLETED"
    assert planner.calls == 1
    assert len(flow.video_requests) == 4
    assert Path(result["final_video"]).is_file()
    assert all(item["status"] == "COMPLETED" for item in result["shot_results"].values())

    manifest = renderer.manifests[-1]
    assert manifest.overlay_image == str(logo.resolve())
    ordered_job_ids = [Path(clip.path).stem for clip in manifest.clips]
    assert "shot_02" in ordered_job_ids[0]
    assert "shot_01" in ordered_job_ids[1]
    assert "shot_03" in ordered_job_ids[2]
    assert "shot_04" in ordered_job_ids[3]


def test_failed_shot_requires_paid_retry_and_keeps_completed_checkpoint(tmp_path):
    model, product, logo = _inputs(tmp_path)
    pipeline, flow, planner, _ = _pipeline(tmp_path, fail_once_shot="shot_02")

    with pytest.raises(RuntimeError, match="simulated paid Flow failure"):
        pipeline.run(
            job_id="factory-retry",
            model_image=str(model),
            product_image=str(product),
            logo_image=str(logo),
            approve_video_credits=True,
        )

    assert len(flow.video_requests) == 2
    state = pipeline.job_store.load("factory-retry")
    assert state is not None
    assert state.shot_results["shot_01"]["status"] == "COMPLETED"
    assert state.shot_results["shot_02"]["status"] == "FAILED"

    with pytest.raises(PaidShotRetryApprovalRequired):
        pipeline.run(
            job_id="factory-retry",
            model_image=str(model),
            product_image=str(product),
            logo_image=str(logo),
            approve_video_credits=True,
        )
    assert len(flow.video_requests) == 2

    result = pipeline.run(
        job_id="factory-retry",
        model_image=str(model),
        product_image=str(product),
        logo_image=str(logo),
        approve_video_credits=True,
        approve_paid_retry=True,
    )

    assert result["status"] == "COMPLETED"
    assert planner.calls == 1
    # First run submitted shot_01 + failing shot_02. Retry submits shot_02, 03, 04.
    assert len(flow.video_requests) == 5
    shot_01_submissions = [r for r in flow.video_requests if "shot_01" in r.job_id]
    assert len(shot_01_submissions) == 1
