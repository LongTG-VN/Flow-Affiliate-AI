import base64
import time
from pathlib import Path

from fastapi.testclient import TestClient

from flow_affiliate_ai.jobs import AffiliateJobState, JobStore
from flow_affiliate_ai.providers.flow.base import FlowHealth
from flow_affiliate_ai.providers.tts.base import TTSHealth
from flow_affiliate_ai.web.app import create_app


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZxVQAAAAASUVORK5CYII="
)


class _HealthProvider:
    def __init__(self, health):
        self._health = health

    def health(self):
        return self._health


class _Service:
    def __init__(self, provider):
        self.provider = provider


class _WebPipeline:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self.flow = _Service(_HealthProvider(FlowHealth(True, True, True, "ready")))
        self.voice = _Service(_HealthProvider(TTSHealth(True, "ready")))

    def run(self, *, job_id, character_image, product_image, **_kwargs):
        run_root = self.data_root / "runs" / job_id
        images = run_root / "images"
        clips = run_root / "clips"
        audio = run_root / "audio"
        renders = run_root / "renders"
        for directory in (images, clips, audio, renders):
            directory.mkdir(parents=True, exist_ok=True)

        isolated = images / "product_isolated.png"
        dressed = images / "character_wearing_product.png"
        character_video = clips / "character.mp4"
        product_video = clips / "product.mp4"
        voice = audio / "voice.wav"
        final = renders / "final_video.mp4"
        isolated.write_bytes(PNG_1X1)
        dressed.write_bytes(PNG_1X1)
        character_video.write_bytes(b"character-video")
        product_video.write_bytes(b"product-video")
        voice.write_bytes(b"voice")
        final.write_bytes(b"final-video")

        state = AffiliateJobState(
            job_id=job_id,
            character_image=str(Path(character_image).resolve()),
            product_image=str(Path(product_image).resolve()),
            status="COMPLETED",
            isolated_product_image=str(isolated.resolve()),
            character_wear_image=str(dressed.resolve()),
            character_video=str(character_video.resolve()),
            product_video=str(product_video.resolve()),
            voice_audio=str(voice.resolve()),
            final_video=str(final.resolve()),
        )
        JobStore(self.data_root / "jobs").save(state)
        return state


def _builder(**kwargs):
    return _WebPipeline(kwargs["data_root"])


def _wait_until_finished(client: TestClient, job_id: str):
    last = None
    for _ in range(100):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        last = response.json()
        if not last["running"] and last["status"] != "QUEUED":
            return last
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {last}")


def test_web_upload_to_final_asset_end_to_end(tmp_path):
    app = create_app(data_root=tmp_path / "data", pipeline_builder=_builder)
    client = TestClient(app)

    created = client.post(
        "/api/jobs",
        data={
            "job_id": "job-web-e2e",
            "approve_video_credits": "true",
            "tts_provider": "gemini",
            "voice": "Zephyr",
            "product_video_style": "zoom",
            "max_credit_per_video": "15",
        },
        files={
            "character": ("character.png", PNG_1X1, "image/png"),
            "product": ("product.png", PNG_1X1, "image/png"),
        },
    )
    assert created.status_code == 202

    job = _wait_until_finished(client, "job-web-e2e")
    assert job["status"] == "COMPLETED"
    assert set(job["assets"]) == {
        "isolated_product",
        "character_wear",
        "character_video",
        "product_video",
        "voice_audio",
        "final_video",
    }

    final = client.get(job["assets"]["final_video"])
    assert final.status_code == 200
    assert final.content == b"final-video"

    duplicate = client.post(
        "/api/jobs",
        data={"job_id": "job-web-e2e", "approve_video_credits": "true"},
        files={
            "character": ("character.png", PNG_1X1, "image/png"),
            "product": ("product.png", PNG_1X1, "image/png"),
        },
    )
    assert duplicate.status_code == 409


def test_web_requires_explicit_credit_approval(tmp_path):
    app = create_app(data_root=tmp_path / "data", pipeline_builder=_builder)
    client = TestClient(app)

    response = client.post(
        "/api/jobs",
        files={
            "character": ("character.png", PNG_1X1, "image/png"),
            "product": ("product.png", PNG_1X1, "image/png"),
        },
    )
    assert response.status_code == 400
    assert "credit approval" in response.json()["detail"].lower()


def test_web_rejects_invalid_job_id_and_generation_options(tmp_path):
    app = create_app(data_root=tmp_path / "data", pipeline_builder=_builder)
    client = TestClient(app)

    invalid_id = client.post(
        "/api/jobs",
        data={"job_id": "../escape", "approve_video_credits": "true"},
        files={
            "character": ("character.png", PNG_1X1, "image/png"),
            "product": ("product.png", PNG_1X1, "image/png"),
        },
    )
    assert invalid_id.status_code == 400

    invalid_style = client.post(
        "/api/jobs",
        data={
            "job_id": "job-invalid-style",
            "approve_video_credits": "true",
            "product_video_style": "macro-extreme",
        },
        files={
            "character": ("character.png", PNG_1X1, "image/png"),
            "product": ("product.png", PNG_1X1, "image/png"),
        },
    )
    assert invalid_style.status_code == 400
