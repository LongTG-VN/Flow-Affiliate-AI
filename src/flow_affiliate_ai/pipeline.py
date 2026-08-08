from dataclasses import asdict
from pathlib import Path
from typing import Optional

from flow_affiliate_ai.jobs import AffiliateJobState, JobStore
from flow_affiliate_ai.prompts.fashion import (
    DEFAULT_VOICE_SCRIPT,
    EXTRACT_PRODUCT_PROMPT,
    PRODUCT_VIDEO_PROMPTS,
    WEAR_PRODUCT_PROMPT,
    character_video_attempt,
    fallback_level,
)
from flow_affiliate_ai.services.core import FlowService, RenderService, VoiceService


class AffiliatePipelineError(RuntimeError):
    pass


class PaidRetryApprovalRequired(AffiliatePipelineError):
    def __init__(self, job_id: str, failed_level: int, next_level: Optional[int]) -> None:
        self.job_id = job_id
        self.failed_level = failed_level
        self.next_level = next_level
        if next_level is None:
            message = (
                f"Character video failed at level {failed_level}; no simpler fallback remains."
            )
        else:
            message = (
                f"Character video failed at level {failed_level}. "
                f"Retry with level {next_level} requires explicit paid-retry approval."
            )
        super().__init__(message)


class AffiliatePipeline:
    """End-to-end fashion affiliate pipeline with durable checkpoints.

    Fixed prompt templates are used for image/video generation. The operator only
    supplies a character image and product image. Paid Flow video retries are never
    performed silently: the first attempt can be approved for the run, while a
    fallback attempt requires `approve_paid_retry=True`.
    """

    def __init__(
        self,
        flow: FlowService,
        voice: VoiceService,
        render: RenderService,
        data_root: Path = Path("data"),
        job_store: Optional[JobStore] = None,
    ) -> None:
        self.flow = flow
        self.voice = voice
        self.render = render
        self.data_root = data_root.resolve()
        self.job_store = job_store or JobStore(self.data_root / "jobs")

    def _workspace(self, job_id: str) -> Path:
        workspace = self.data_root / "runs" / job_id
        for child in ("images", "clips", "audio", "renders"):
            (workspace / child).mkdir(parents=True, exist_ok=True)
        return workspace

    @staticmethod
    def _require_input(path: str, label: str) -> str:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise AffiliatePipelineError(f"{label} does not exist: {resolved}")
        return str(resolved)

    def _load_or_create(
        self,
        job_id: str,
        character_image: str,
        product_image: str,
    ) -> AffiliateJobState:
        character = self._require_input(character_image, "character image")
        product = self._require_input(product_image, "product image")
        existing = self.job_store.load(job_id)
        if existing:
            if Path(existing.character_image).resolve() != Path(character).resolve():
                raise AffiliatePipelineError("job character image cannot be changed")
            if Path(existing.product_image).resolve() != Path(product).resolve():
                raise AffiliatePipelineError("job product image cannot be changed")
            return existing
        state = AffiliateJobState(
            job_id=job_id,
            character_image=character,
            product_image=product,
        )
        self.job_store.save(state)
        return state

    def _extract_product(self, state: AffiliateJobState, workspace: Path) -> None:
        if state.isolated_product_image and Path(state.isolated_product_image).is_file():
            return
        result = self.flow.generate_image(
            job_id=f"{state.job_id}-extract-product",
            prompt=EXTRACT_PRODUCT_PROMPT,
            reference_paths=[state.product_image],
            output_path=str(workspace / "images" / "product_isolated.png"),
            aspect_ratio="9:16",
        )
        if result.status != "COMPLETED" or not result.output_path:
            raise AffiliatePipelineError(result.error_message or "product extraction failed")
        state.isolated_product_image = result.output_path
        state.status = "PRODUCT_EXTRACTED"
        self.job_store.clear_error(state)

    def _dress_character(self, state: AffiliateJobState, workspace: Path) -> None:
        if state.character_wear_image and Path(state.character_wear_image).is_file():
            return
        if not state.isolated_product_image:
            raise AffiliatePipelineError("isolated product image is missing")
        result = self.flow.generate_image(
            job_id=f"{state.job_id}-dress-character",
            prompt=WEAR_PRODUCT_PROMPT,
            reference_paths=[state.character_image, state.isolated_product_image],
            output_path=str(workspace / "images" / "character_wearing_product.png"),
            aspect_ratio="9:16",
        )
        if result.status != "COMPLETED" or not result.output_path:
            raise AffiliatePipelineError(result.error_message or "character dressing failed")
        state.character_wear_image = result.output_path
        state.status = "CHARACTER_DRESSED"
        self.job_store.clear_error(state)

    def _video_output(self, provider_job_id: str) -> str:
        status = self.flow.provider.poll(provider_job_id)
        if status.status != "COMPLETED" or not status.output_paths:
            raise AffiliatePipelineError(
                status.error_message or f"Flow video has no output: {provider_job_id}"
            )
        output = Path(status.output_paths[0]).resolve()
        if not output.is_file():
            raise AffiliatePipelineError(f"Flow video output missing: {output}")
        return str(output)

    def _generate_character_video(
        self,
        state: AffiliateJobState,
        workspace: Path,
        *,
        approve_video_credits: bool,
        approve_paid_retry: bool,
        max_credit_per_video: int,
    ) -> None:
        if state.character_video and Path(state.character_video).is_file():
            return
        if not approve_video_credits:
            raise AffiliatePipelineError(
                "video credit approval required: set approve_video_credits=True"
            )
        if not state.character_wear_image:
            raise AffiliatePipelineError("character wearing product image is missing")

        level = state.character_video_level
        if level < 3 and not approve_paid_retry:
            raise PaidRetryApprovalRequired(state.job_id, level + 1, level)
        attempt = character_video_attempt(level)
        provider_job_id = f"{state.job_id}-character-l{level}"
        try:
            ref = self.flow.generate_video(
                job_id=provider_job_id,
                prompt=attempt.prompt,
                image_path=state.character_wear_image,
                duration_seconds=10,
                output_directory=str(workspace / "clips" / f"character-l{level}"),
                max_credit_cost=max_credit_per_video,
            )
            state.character_video = self._video_output(ref.provider_job_id)
            state.status = "CHARACTER_VIDEO_READY"
            self.job_store.clear_error(state)
        except Exception as exc:
            next_level = fallback_level(level)
            self.job_store.mark_error(state, "CHARACTER_VIDEO", str(exc))
            if next_level is not None:
                state.character_video_level = next_level
                self.job_store.save(state)
            raise PaidRetryApprovalRequired(state.job_id, level, next_level) from exc

    def _generate_product_video(
        self,
        state: AffiliateJobState,
        workspace: Path,
        *,
        style: str,
        approve_video_credits: bool,
        max_credit_per_video: int,
    ) -> None:
        if state.product_video and Path(state.product_video).is_file():
            return
        if not approve_video_credits:
            raise AffiliatePipelineError(
                "video credit approval required: set approve_video_credits=True"
            )
        if style not in PRODUCT_VIDEO_PROMPTS:
            raise AffiliatePipelineError(f"unknown product video style: {style}")
        if not state.isolated_product_image:
            raise AffiliatePipelineError("isolated product image is missing")
        ref = self.flow.generate_video(
            job_id=f"{state.job_id}-product-{style}",
            prompt=PRODUCT_VIDEO_PROMPTS[style],
            image_path=state.isolated_product_image,
            duration_seconds=10,
            output_directory=str(workspace / "clips" / f"product-{style}"),
            max_credit_cost=max_credit_per_video,
        )
        state.product_video = self._video_output(ref.provider_job_id)
        state.status = "PRODUCT_VIDEO_READY"
        self.job_store.clear_error(state)

    def _synthesize_voice(
        self,
        state: AffiliateJobState,
        workspace: Path,
        *,
        script: str,
        voice: str,
    ) -> None:
        if state.voice_audio and Path(state.voice_audio).is_file():
            return
        result = self.voice.synthesize(
            job_id=f"{state.job_id}-voice",
            text=script,
            voice=voice,
            output_directory=str(workspace / "audio"),
        )
        if result.status != "COMPLETED" or not result.audio_path:
            raise AffiliatePipelineError(result.error_message or "TTS synthesis failed")
        state.voice_audio = result.audio_path
        state.status = "VOICE_READY"
        self.job_store.clear_error(state)

    def _render(
        self,
        state: AffiliateJobState,
        workspace: Path,
        *,
        music_track: Optional[str],
        captions_ass: Optional[str],
    ) -> None:
        if state.final_video and Path(state.final_video).is_file():
            return
        if not state.character_video or not state.product_video:
            raise AffiliatePipelineError("both generated video clips are required")
        resolved_music = None
        if music_track:
            resolved_music = self._require_input(music_track, "music track")
        resolved_captions = None
        if captions_ass:
            resolved_captions = self._require_input(captions_ass, "captions ASS")
        result = self.render.render_vertical(
            job_id=state.job_id,
            clip_paths=[state.character_video, state.product_video],
            voice_track=state.voice_audio,
            music_track=resolved_music,
            captions_ass=resolved_captions,
            output_path=str(workspace / "renders" / "final_video.mp4"),
        )
        if result.status != "COMPLETED" or not result.master_output_path:
            raise AffiliatePipelineError(result.error_message or "final render failed")
        state.final_video = result.master_output_path
        state.status = "COMPLETED"
        self.job_store.clear_error(state)

    def run(
        self,
        *,
        job_id: str,
        character_image: str,
        product_image: str,
        voice_script: str = DEFAULT_VOICE_SCRIPT,
        voice: str = "Zephyr",
        product_video_style: str = "zoom",
        music_track: Optional[str] = None,
        captions_ass: Optional[str] = None,
        approve_video_credits: bool = False,
        approve_paid_retry: bool = False,
        max_credit_per_video: int = 15,
    ) -> dict:
        state = self._load_or_create(job_id, character_image, product_image)
        workspace = self._workspace(job_id)
        try:
            self._extract_product(state, workspace)
            self._dress_character(state, workspace)
            self._generate_character_video(
                state,
                workspace,
                approve_video_credits=approve_video_credits,
                approve_paid_retry=approve_paid_retry,
                max_credit_per_video=max_credit_per_video,
            )
            self._generate_product_video(
                state,
                workspace,
                style=product_video_style,
                approve_video_credits=approve_video_credits,
                max_credit_per_video=max_credit_per_video,
            )
            self._synthesize_voice(
                state,
                workspace,
                script=voice_script,
                voice=voice,
            )
            self._render(
                state,
                workspace,
                music_track=music_track,
                captions_ass=captions_ass,
            )
            return asdict(state)
        except Exception as exc:
            if not isinstance(exc, PaidRetryApprovalRequired):
                self.job_store.mark_error(state, state.status, str(exc))
            raise
