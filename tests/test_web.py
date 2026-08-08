from pathlib import Path

from fastapi.testclient import TestClient

from flow_affiliate_ai.jobs import AffiliateJobState
from flow_affiliate_ai.providers.flow.base import FlowHealth
from flow_affiliate_ai.providers.tts.base import TTSHealth
from flow_affiliate_ai.web.app import create_app


class _HealthProvider:
    def __init__(self, health):
        self._health = health

    def health(self):
        return self._health


class _Service:
    def __init__(self, provider):
        self.provider = provider


class _FakePipeline:
    def __init__(self):
        self.flow = _Service(_HealthProvider(FlowHealth(True, True, True, "ready")))
        self.voice = _Service(_HealthProvider(TTSHealth(True, "ready")))

    def run(self, **_kwargs):
        return {"status": "COMPLETED"}


def _builder(**_kwargs):
    return _FakePipeline()


def test_dashboard_and_health(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert "Flow Affiliate AI" in response.text
    assert 'name="extract_product_prompt"' in response.text
    assert 'name="wear_product_prompt"' in response.text
    assert 'name="character_video_prompt"' in response.text
    assert 'name="product_video_prompt"' in response.text
    assert 'name="product_video_source"' in response.text
    assert 'name="sticker"' in response.text
    assert 'name="overlay_position"' in response.text
    assert 'name="overlay_width_pct"' in response.text

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["flow"]["healthy"] is True
    assert health.json()["tts"]["healthy"] is True


def test_prompt_defaults_support_product_style(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)

    zoom = client.get("/api/prompts/defaults?product_video_style=zoom")
    pan = client.get("/api/prompts/defaults?product_video_style=pan")

    assert zoom.status_code == 200
    assert pan.status_code == 200
    assert set(zoom.json()) == {
        "extract_product",
        "wear_product",
        "character_video",
        "product_video",
    }
    assert zoom.json()["product_video"] != pan.json()["product_video"]


def test_dashboard_persists_four_custom_prompts(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)

    response = client.post(
        "/api/jobs",
        data={
            "job_id": "job-prompts-1",
            "approve_video_credits": "true",
            "extract_product_prompt": "CUSTOM EXTRACT",
            "wear_product_prompt": "CUSTOM WEAR",
            "character_video_prompt": "CUSTOM CHARACTER VIDEO",
            "product_video_prompt": "CUSTOM PRODUCT VIDEO",
        },
        files={
            "character": ("character.png", b"fake-character", "image/png"),
            "product": ("product.png", b"fake-product", "image/png"),
        },
    )

    assert response.status_code == 202
    config = app.state.runner.configs.load("job-prompts-1")
    assert config is not None
    assert config.prompts == {
        "extract_product": "CUSTOM EXTRACT",
        "wear_product": "CUSTOM WEAR",
        "character_video": "CUSTOM CHARACTER VIDEO",
        "product_video": "CUSTOM PRODUCT VIDEO",
    }

    job = client.get("/api/jobs/job-prompts-1")
    assert job.status_code == 200
    assert job.json()["prompts"] == config.prompts


def test_dashboard_persists_sticker_and_product_source(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)

    response = client.post(
        "/api/jobs",
        data={
            "job_id": "job-overlay-1",
            "approve_video_credits": "true",
            "product_video_source": "isolated",
            "overlay_position": "top-right",
            "overlay_width_pct": "26",
        },
        files={
            "character": ("character.png", b"fake-character", "image/png"),
            "product": ("product.png", b"fake-product", "image/png"),
            "sticker": ("sticker.webp", b"fake-sticker", "image/webp"),
        },
    )

    assert response.status_code == 202
    config = app.state.runner.configs.load("job-overlay-1")
    assert config is not None
    assert config.product_video_source == "isolated"
    assert config.overlay_position == "top-right"
    assert config.overlay_width_pct == 26
    assert config.overlay_image is not None
    assert Path(config.overlay_image).name == "sticker.webp"
    assert Path(config.overlay_image).read_bytes() == b"fake-sticker"

    job = client.get("/api/jobs/job-overlay-1")
    assert job.status_code == 200
    assert job.json()["render_options"] == {
        "product_video_source": "isolated",
        "music_enabled": False,
        "overlay_enabled": True,
        "overlay_position": "top-right",
        "overlay_width_pct": 26.0,
    }


def test_dashboard_persists_music_track(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)

    response = client.post(
        "/api/jobs",
        data={
            "job_id": "job-music-1",
            "approve_video_credits": "true",
        },
        files={
            "character": ("character.png", b"fake-character", "image/png"),
            "product": ("product.png", b"fake-product", "image/png"),
            "music": ("bgm.mp3", b"fake-audio-data", "audio/mp3"),
        },
    )

    assert response.status_code == 202
    config = app.state.runner.configs.load("job-music-1")
    assert config is not None
    assert config.music_track is not None
    assert Path(config.music_track).name == "music.mp3"
    assert Path(config.music_track).read_bytes() == b"fake-audio-data"

    job = client.get("/api/jobs/job-music-1")
    assert job.status_code == 200
    assert job.json()["render_options"]["music_enabled"] is True


def test_rejects_empty_prompt(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)

    response = client.post(
        "/api/jobs",
        data={
            "approve_video_credits": "true",
            "extract_product_prompt": "   ",
        },
        files={
            "character": ("character.png", b"fake", "image/png"),
            "product": ("product.png", b"fake", "image/png"),
        },
    )
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"]


def test_rejects_non_image_upload(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)

    response = client.post(
        "/api/jobs",
        data={"approve_video_credits": "true"},
        files={
            "character": ("character.txt", b"not an image", "text/plain"),
            "product": ("product.png", b"fake", "image/png"),
        },
    )
    assert response.status_code == 400


def test_rejects_invalid_overlay_settings(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)

    response = client.post(
        "/api/jobs",
        data={
            "approve_video_credits": "true",
            "overlay_position": "outside",
            "overlay_width_pct": "90",
        },
        files={
            "character": ("character.png", b"fake", "image/png"),
            "product": ("product.png", b"fake", "image/png"),
        },
    )
    assert response.status_code == 400


def test_asset_endpoint_cannot_escape_data_root(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)
    outside = tmp_path.parent / "outside-test.mp4"
    outside.write_bytes(b"outside")
    try:
        state = AffiliateJobState(
            job_id="job-safe-1",
            character_image="character.png",
            product_image="product.png",
            final_video=str(outside),
        )
        app.state.runner.jobs.save(state)
        response = client.get("/api/jobs/job-safe-1/assets/final_video")
        assert response.status_code == 403
    finally:
        outside.unlink(missing_ok=True)


def test_asset_endpoint_serves_only_local_job_asset(tmp_path: Path):
    app = create_app(data_root=tmp_path, pipeline_builder=_builder)
    client = TestClient(app)
    final = tmp_path / "runs" / "job-safe-2" / "renders" / "final_video.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")
    state = AffiliateJobState(
        job_id="job-safe-2",
        character_image="character.png",
        product_image="product.png",
        final_video=str(final),
    )
    app.state.runner.jobs.save(state)

    response = client.get("/api/jobs/job-safe-2/assets/final_video")
    assert response.status_code == 200
    assert response.content == b"video"
