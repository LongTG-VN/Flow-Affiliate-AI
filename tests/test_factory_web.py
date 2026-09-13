from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient

from flow_affiliate_ai.factory_web.app import create_app
from flow_affiliate_ai.jobs import AffiliateJobState, JobStore


PLAN = {
    "schema_version": "1.0",
    "project_type": "fashion_affiliate",
    "creative_summary": "fake web plan",
    "wear_model_prompt": "same model exact product",
    "total_shots": 4,
    "shots": [
        {
            "shot_id": f"shot_{i:02d}",
            "purpose": purpose,
            "source_type": "isolated_product" if purpose == "detail" else "worn_model",
            "duration_seconds": 4,
            "flow_prompt": f"PROMPT {i}",
        }
        for i, purpose in enumerate(("hook", "showcase", "detail", "ending"), start=1)
    ],
    "edit_sequence": ["shot_01", "shot_02", "shot_03", "shot_04"],
}


class FakeFlowHealth:
    healthy = True
    chrome_reachable = True
    logged_in = True
    message = "ok"


class FakeFlowProvider:
    def health(self):
        return FakeFlowHealth()


class FakeFlowService:
    provider = FakeFlowProvider()


class FakeFactoryPipeline:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()
        self.job_store = JobStore(self.data_root / "jobs")
        self.flow = FakeFlowService()

    def run(
        self,
        *,
        job_id: str,
        model_image: str,
        product_image: str,
        logo_image: str,
        approve_video_credits: bool = False,
        approve_paid_retry: bool = False,
        **kwargs,
    ):
        state = self.job_store.load(job_id)
        if state is None:
            state = AffiliateJobState(
                job_id=job_id,
                character_image=str(Path(model_image).resolve()),
                product_image=str(Path(product_image).resolve()),
                logo_image=str(Path(logo_image).resolve()),
            )
        run = self.data_root / "runs" / job_id
        images = run / "images"
        renders = run / "renders"
        images.mkdir(parents=True, exist_ok=True)
        renders.mkdir(parents=True, exist_ok=True)
        isolated = images / "product_isolated.png"
        worn = images / "model_wearing_product.png"
        isolated.write_bytes(b"isolated")
        worn.write_bytes(b"worn")
        state.isolated_product_image = str(isolated.resolve())
        state.canonical_worn_image = str(worn.resolve())
        state.character_wear_image = str(worn.resolve())
        state.shot_plan = PLAN
        state.metadata["estimated_total_credits"] = 24
        state.metadata["shot_credit_estimates"] = {
            f"shot_{i:02d}": 6 for i in range(1, 5)
        }
        if not approve_video_credits:
            state.status = "PLAN_READY"
            self.job_store.save(state)
            return asdict(state)

        for shot in PLAN["shots"]:
            clip_dir = run / "clips" / shot["shot_id"]
            clip_dir.mkdir(parents=True, exist_ok=True)
            clip = clip_dir / f"{shot['shot_id']}.mp4"
            clip.write_bytes(b"video")
            state.shot_results[shot["shot_id"]] = {
                "status": "COMPLETED",
                "output_path": str(clip.resolve()),
                "attempt": 1,
            }
        final = renders / "final_video.mp4"
        final.write_bytes(b"final")
        state.final_video = str(final.resolve())
        state.status = "COMPLETED"
        self.job_store.save(state)
        return asdict(state)


def _builder(data_root: Path):
    return FakeFactoryPipeline(data_root)


def test_factory_web_three_input_plan_approve_and_final_asset(tmp_path):
    app = create_app(data_root=tmp_path / "data", pipeline_builder=_builder)
    client = TestClient(app)

    response = client.post(
        "/api/jobs",
        files={
            "model": ("model.png", b"model", "image/png"),
            "product": ("dress.png", b"dress", "image/png"),
            "logo": ("logo.png", b"logo", "image/png"),
        },
        data={"job_id": "web-factory-001"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    app.state.runner._futures[job_id].result(timeout=2)

    planned = client.get(f"/api/jobs/{job_id}")
    assert planned.status_code == 200
    payload = planned.json()
    assert payload["status"] == "PLAN_READY"
    assert payload["shot_plan"]["total_shots"] == 4
    assert payload["metadata"]["estimated_total_credits"] == 24

    approved = client.post(f"/api/jobs/{job_id}/approve", data={})
    assert approved.status_code == 202
    app.state.runner._futures[job_id].result(timeout=2)

    completed = client.get(f"/api/jobs/{job_id}").json()
    assert completed["status"] == "COMPLETED"
    assert all(item["status"] == "COMPLETED" for item in completed["shot_results"].values())

    final = client.get(f"/api/jobs/{job_id}/assets/final_video")
    assert final.status_code == 200
    assert final.content == b"final"


def test_factory_web_rejects_missing_or_invalid_image_inputs(tmp_path):
    app = create_app(data_root=tmp_path / "data", pipeline_builder=_builder)
    client = TestClient(app)

    missing = client.post(
        "/api/jobs",
        files={"model": ("model.png", b"model", "image/png")},
    )
    assert missing.status_code == 422

    invalid = client.post(
        "/api/jobs",
        files={
            "model": ("model.gif", b"model", "image/gif"),
            "product": ("dress.png", b"dress", "image/png"),
            "logo": ("logo.png", b"logo", "image/png"),
        },
    )
    assert invalid.status_code == 400
