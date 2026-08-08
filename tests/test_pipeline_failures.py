from pathlib import Path

import pytest

from flow_affiliate_ai.pipeline import AffiliatePipeline, AffiliatePipelineError, PaidRetryApprovalRequired
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


class RecordingFlowProvider:
    def __init__(self, *, fail_character_l3_once=False, fail_product_once=False, fail_extract_once=False):
        self.outputs = {}
        self.image_jobs = []
        self.video_jobs = []
        self.fail_character_l3_once = fail_character_l3_once
        self.fail_product_once = fail_product_once
        self.fail_extract_once = fail_extract_once

    def capabilities(self):
        return ProviderCapabilities()

    def health(self):
        return FlowHealth(True, True, True, "ok")

    def generate_image(self, request):
        self.image_jobs.append(request.job_id)
        if self.fail_extract_once and "extract-product" in request.job_id:
            self.fail_extract_once = False
            raise RuntimeError("simulated extract failure")
        path = Path(request.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
        self.outputs[request.job_id] = [str(path.resolve())]
        return FlowImageResult(request.job_id, "COMPLETED", str(path.resolve()))

    def estimate(self, request):
        return CostQuote(15, request.max_credit_cost, 15 <= request.max_credit_cost)

    def submit(self, request):
        self.video_jobs.append(request.job_id)
        if self.fail_character_l3_once and request.job_id.endswith("character-l3"):
            self.fail_character_l3_once = False
            raise RuntimeError("simulated L3 failure")
        if self.fail_product_once and "-product-" in request.job_id:
            self.fail_product_once = False
            raise RuntimeError("simulated product failure")
        out = Path(request.output_directory) / f"{request.job_id}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"video")
        self.outputs[request.job_id] = [str(out.resolve())]
        return ProviderJobRef(request.job_id, "COMPLETED", 15)

    def poll(self, provider_job_id):
        return ProviderJobStatus(
            provider_job_id,
            "COMPLETED" if provider_job_id in self.outputs else "FAILED",
            100 if provider_job_id in self.outputs else 0,
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
        path.write_bytes(b"audio")
        return TTSResult(request.job_id, "COMPLETED", str(path.resolve()), None, 1000, "audio/wav")


class FakeRenderProvider:
    def validate_inputs(self, manifest):
        return ValidationResult(True, [])

    def render(self, manifest):
        path = Path(manifest.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"final")
        return RenderJobRef(manifest.job_id, "COMPLETED", str(path.resolve()), "sha")

    def probe(self, output):
        raise NotImplementedError


def make_pipeline(tmp_path: Path, flow_provider: RecordingFlowProvider):
    return AffiliatePipeline(
        flow=FlowService(flow_provider),
        voice=VoiceService(FakeTTSProvider()),
        render=RenderService(FakeRenderProvider()),
        data_root=tmp_path / "data",
    )


def inputs(tmp_path: Path):
    character = tmp_path / "character.png"
    product = tmp_path / "product.png"
    character.write_bytes(b"character")
    product.write_bytes(b"product")
    return str(character), str(product)


def test_character_fallback_requires_explicit_retry_and_keeps_checkpoints(tmp_path):
    provider = RecordingFlowProvider(fail_character_l3_once=True)
    pipeline = make_pipeline(tmp_path, provider)
    character, product = inputs(tmp_path)

    with pytest.raises(PaidRetryApprovalRequired) as failure:
        pipeline.run(
            job_id="job-fallback",
            character_image=character,
            product_image=product,
            approve_video_credits=True,
        )

    assert failure.value.failed_level == 3
    assert failure.value.next_level == 2
    state = pipeline.job_store.load("job-fallback")
    assert state.character_video_level == 2
    assert state.isolated_product_image
    assert state.character_wear_image
    assert len(provider.image_jobs) == 2

    with pytest.raises(PaidRetryApprovalRequired):
        pipeline.run(
            job_id="job-fallback",
            character_image=character,
            product_image=product,
            approve_video_credits=True,
            approve_paid_retry=False,
        )

    assert len(provider.image_jobs) == 2

    result = pipeline.run(
        job_id="job-fallback",
        character_image=character,
        product_image=product,
        approve_video_credits=True,
        approve_paid_retry=True,
    )

    assert result["status"] == "COMPLETED"
    assert len(provider.image_jobs) == 2
    assert provider.video_jobs[0].endswith("character-l3")
    assert any(job.endswith("character-l2") for job in provider.video_jobs)


def test_product_video_failure_requires_explicit_paid_retry(tmp_path):
    provider = RecordingFlowProvider(fail_product_once=True)
    pipeline = make_pipeline(tmp_path, provider)
    character, product = inputs(tmp_path)

    with pytest.raises(AffiliatePipelineError, match="product video failed"):
        pipeline.run(
            job_id="job-product-retry",
            character_image=character,
            product_image=product,
            approve_video_credits=True,
        )

    state = pipeline.job_store.load("job-product-retry")
    assert state.character_video
    assert state.product_video_attempt == 2

    with pytest.raises(AffiliatePipelineError, match="approve_paid_retry"):
        pipeline.run(
            job_id="job-product-retry",
            character_image=character,
            product_image=product,
            approve_video_credits=True,
        )

    result = pipeline.run(
        job_id="job-product-retry",
        character_image=character,
        product_image=product,
        approve_video_credits=True,
        approve_paid_retry=True,
    )
    assert result["status"] == "COMPLETED"
    assert any("product-zoom-a2" in job for job in provider.video_jobs)


def test_image_failure_uses_new_attempt_without_repeating_completed_stages(tmp_path):
    provider = RecordingFlowProvider(fail_extract_once=True)
    pipeline = make_pipeline(tmp_path, provider)
    character, product = inputs(tmp_path)

    with pytest.raises(RuntimeError, match="extract failure"):
        pipeline.run(
            job_id="job-image-retry",
            character_image=character,
            product_image=product,
            approve_video_credits=True,
        )

    state = pipeline.job_store.load("job-image-retry")
    assert state.extract_attempt == 2

    result = pipeline.run(
        job_id="job-image-retry",
        character_image=character,
        product_image=product,
        approve_video_credits=True,
    )

    assert result["status"] == "COMPLETED"
    assert provider.image_jobs[0].endswith("extract-product-a1")
    assert provider.image_jobs[1].endswith("extract-product-a2")
