from pathlib import Path

import pytest

from flow_affiliate_ai.pipeline import AffiliatePipeline, AffiliatePipelineError
from flow_affiliate_ai.providers.flow.base import (
    CostQuote,
    FlowHealth,
    FlowImageResult,
    ProviderCapabilities,
    ProviderJobRef,
    ProviderJobStatus,
)
from flow_affiliate_ai.providers.render.base import RenderJobRef, ValidationResult
from flow_affiliate_ai.providers.tts.base import TTSHealth, TTSResult
from flow_affiliate_ai.services.core import FlowService, RenderService, VoiceService


class FakeFlowProvider:
    def __init__(self) -> None:
        self.outputs = {}
        self.image_prompts = []
        self.video_prompts = []

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
        return CostQuote(15, request.max_credit_cost, True)

    def submit(self, request):
        self.video_prompts.append(request.prompt)
        out = Path(request.output_directory) / f"{request.job_id}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-video")
        self.outputs[request.job_id] = [str(out.resolve())]
        return ProviderJobRef(request.job_id, "COMPLETED", 15)

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


class FakeTTSProvider:
    def health(self):
        return TTSHealth(True, "ok")

    def synthesize(self, request):
        path = Path(request.output_directory) / "voice.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-audio")
        return TTSResult(
            job_id=request.job_id,
            status="COMPLETED",
            audio_path=str(path.resolve()),
            boundary_path=None,
            duration_ms=1000,
            mime_type="audio/wav",
        )


class FakeRenderProvider:
    def validate_inputs(self, manifest):
        return ValidationResult(True, [])

    def render(self, manifest):
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


def _pipeline(tmp_path: Path) -> AffiliatePipeline:
    return AffiliatePipeline(
        flow=FlowService(FakeFlowProvider()),
        voice=VoiceService(FakeTTSProvider()),
        render=RenderService(FakeRenderProvider()),
        data_root=tmp_path / "data",
    )


def _inputs(tmp_path: Path):
    character = tmp_path / "character.png"
    product = tmp_path / "product.png"
    character.write_bytes(b"character")
    product.write_bytes(b"product")
    return character, product


def test_end_to_end_pipeline_produces_final_video(tmp_path):
    character, product = _inputs(tmp_path)

    result = _pipeline(tmp_path).run(
        job_id="job-001",
        character_image=str(character),
        product_image=str(product),
        approve_video_credits=True,
    )

    assert result["status"] == "COMPLETED"
    assert Path(result["isolated_product_image"]).is_file()
    assert Path(result["character_wear_image"]).is_file()
    assert Path(result["character_video"]).is_file()
    assert Path(result["product_video"]).is_file()
    assert Path(result["voice_audio"]).is_file()
    assert Path(result["final_video"]).is_file()
    assert set(result["prompts"]) == {
        "extract_product",
        "wear_product",
        "character_video",
        "product_video",
    }


def test_pipeline_uses_four_custom_prompts(tmp_path):
    character, product = _inputs(tmp_path)
    pipeline = _pipeline(tmp_path)
    provider = pipeline.flow.provider
    prompts = {
        "extract_product_prompt": "CUSTOM EXTRACT",
        "wear_product_prompt": "CUSTOM WEAR",
        "character_video_prompt": "CUSTOM CHARACTER VIDEO",
        "product_video_prompt": "CUSTOM PRODUCT VIDEO",
    }

    result = pipeline.run(
        job_id="job-custom-prompts",
        character_image=str(character),
        product_image=str(product),
        approve_video_credits=True,
        **prompts,
    )

    assert provider.image_prompts == ["CUSTOM EXTRACT", "CUSTOM WEAR"]
    assert provider.video_prompts == ["CUSTOM CHARACTER VIDEO", "CUSTOM PRODUCT VIDEO"]
    assert result["prompts"] == {
        "extract_product": "CUSTOM EXTRACT",
        "wear_product": "CUSTOM WEAR",
        "character_video": "CUSTOM CHARACTER VIDEO",
        "product_video": "CUSTOM PRODUCT VIDEO",
    }


def test_pipeline_resumes_from_checkpoints(tmp_path):
    character, product = _inputs(tmp_path)
    pipeline = _pipeline(tmp_path)

    first = pipeline.run(
        job_id="job-resume",
        character_image=str(character),
        product_image=str(product),
        approve_video_credits=True,
    )
    second = pipeline.run(
        job_id="job-resume",
        character_image=str(character),
        product_image=str(product),
        approve_video_credits=True,
    )

    assert second["final_video"] == first["final_video"]
    assert second["status"] == "COMPLETED"


def test_pipeline_rejects_prompt_changes_for_existing_job(tmp_path):
    character, product = _inputs(tmp_path)
    pipeline = _pipeline(tmp_path)

    pipeline.run(
        job_id="job-prompt-lock",
        character_image=str(character),
        product_image=str(product),
        extract_product_prompt="FIRST EXTRACT",
        approve_video_credits=True,
    )

    with pytest.raises(AffiliatePipelineError, match="job prompts cannot be changed"):
        pipeline.run(
            job_id="job-prompt-lock",
            character_image=str(character),
            product_image=str(product),
            extract_product_prompt="SECOND EXTRACT",
            approve_video_credits=True,
        )
