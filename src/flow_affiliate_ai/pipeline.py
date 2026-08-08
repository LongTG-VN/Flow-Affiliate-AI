from dataclasses import asdict
from pathlib import Path
from typing import Optional

from flow_affiliate_ai.jobs import AffiliateJobState, JobStore
from flow_affiliate_ai.prompts.fashion import (
    DEFAULT_VOICE_SCRIPT,
    EXTRACT_PRODUCT_PROMPT,
    MAX_PROMPT_CHARS,
    PRODUCT_VIDEO_PROMPTS,
    WEAR_PRODUCT_PROMPT,
    character_video_attempt,
    fallback_level,
)
from flow_affiliate_ai.services.core import FlowService, RenderService, VoiceService


PRODUCT_VIDEO_SOURCES = {"worn", "isolated"}
OVERLAY_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}


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

    Four primary prompts can be supplied per job from the web UI or another caller.
    The prompts are persisted into the job checkpoint so resumed runs cannot silently
    switch generation instructions halfway through a job. Character-video fallback
    levels 2 and 1 remain fixed, intentionally simpler backend safety prompts.
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

    @staticmethod
    def _resolve_prompt(value: Optional[str], default: str, label: str) -> str:
        prompt = (value if value is not None else default).strip()
        if not prompt:
            raise AffiliatePipelineError(f"{label} cannot be empty")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise AffiliatePipelineError(
                f"{label} exceeds {MAX_PROMPT_CHARS} characters"
            )
        return prompt

    def _load_or_create(
        self,
        job_id: str,
        character_image: str,
        product_image: str,
        prompts: dict[str, str],
        product_video_source: str,
    ) -> AffiliateJobState:
        character = self._require_input(character_image, "character image")
        product = self._require_input(product_image, "product image")
        existing = self.job_store.load(job_id)
        if existing:
            if Path(existing.character_image).resolve() != Path(character).resolve():
                raise AffiliatePipelineError("job character image cannot be changed")
            if Path(existing.product_image).resolve() != Path(product).resolve():
                raise AffiliatePipelineError("job product image cannot be changed")
            if existing.product_video_source != product_video_source:
                raise AffiliatePipelineError(
                    "job product video source cannot be changed after generation has started"
                )
            if existing.prompts:
                for key, saved_prompt in existing.prompts.items():
                    if key in prompts and prompts[key] != saved_prompt:
                        raise AffiliatePipelineError(
                            "job prompts cannot be changed after generation has started; create a new job"
                        )
                if set(existing.prompts) != set(prompts):
                    existing.prompts.update(prompts)
                    self.job_store.save(existing)
            else:
                existing.prompts = dict(prompts)
                self.job_store.save(existing)
            return existing
        state = AffiliateJobState(
            job_id=job_id,
            character_image=character,
            product_image=product,
            prompts=dict(prompts),
            product_video_source=product_video_source,
        )
        self.job_store.save(state)
        return state

    def _extract_product(
        self,
        state: AffiliateJobState,
        workspace: Path,
        *,
        prompt: str,
    ) -> None:
        if state.isolated_product_image and Path(state.isolated_product_image).is_file():
            return
        provider_job_id = f"{state.job_id}-extract-product-a{state.extract_attempt}"
        try:
            result = self.flow.generate_image(
                job_id=provider_job_id,
                prompt=prompt,
                reference_paths=[state.product_image],
                output_path=str(workspace / "images" / "product_isolated.png"),
                aspect_ratio="9:16",
            )
            if result.status != "COMPLETED" or not result.output_path:
                raise AffiliatePipelineError(
                    result.error_message or "product extraction failed"
                )
            state.isolated_product_image = result.output_path
            state.status = "PRODUCT_EXTRACTED"
            self.job_store.clear_error(state)
        except Exception as exc:
            state.extract_attempt += 1
            self.job_store.mark_error(state, "PRODUCT_EXTRACTION", str(exc))
            raise

    def _dress_character(
        self,
        state: AffiliateJobState,
        workspace: Path,
        *,
        prompt: str,
    ) -> None:
        if state.character_wear_image and Path(state.character_wear_image).is_file():
            return
        if not state.isolated_product_image:
            raise AffiliatePipelineError("isolated product image is missing")
        provider_job_id = f"{state.job_id}-dress-character-a{state.dress_attempt}"
        try:
            result = self.flow.generate_image(
                job_id=provider_job_id,
                prompt=prompt,
                reference_paths=[state.character_image, state.isolated_product_image],
                output_path=str(workspace / "images" / "character_wearing_product.png"),
                aspect_ratio="9:16",
            )
            if result.status != "COMPLETED" or not result.output_path:
                raise AffiliatePipelineError(
                    result.error_message or "character dressing failed"
                )
            state.character_wear_image = result.output_path
            state.status = "CHARACTER_DRESSED"
            self.job_store.clear_error(state)
        except Exception as exc:
            state.dress_attempt += 1
            self.job_store.mark_error(state, "CHARACTER_DRESSING", str(exc))
            raise

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
        primary_prompt: str,
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
        prompt = primary_prompt if level == 3 else character_video_attempt(level).prompt
        provider_job_id = f"{state.job_id}-character-l{level}"
        try:
            ref = self.flow.generate_video(
                job_id=provider_job_id,
                prompt=prompt,
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
            if next_level is not None:
                state.character_video_level = next_level
            self.job_store.mark_error(state, "CHARACTER_VIDEO", str(exc))
            raise PaidRetryApprovalRequired(state.job_id, level, next_level) from exc

    def _generate_product_video(
        self,
        state: AffiliateJobState,
        workspace: Path,
        *,
        style: str,
        prompt: str,
        source: str,
        approve_video_credits: bool,
        approve_paid_retry: bool,
        max_credit_per_video: int,
    ) -> None:
        if state.product_video and Path(state.product_video).is_file():
            return
        if not approve_video_credits:
            raise AffiliatePipelineError(
                "video credit approval required: set approve_video_credits=True"
            )
        if state.product_video_attempt > 1 and not approve_paid_retry:
            raise AffiliatePipelineError(
                "product video retry requires approve_paid_retry=True"
            )
        if style not in PRODUCT_VIDEO_PROMPTS:
            raise AffiliatePipelineError(f"unknown product video style: {style}")
        if source not in PRODUCT_VIDEO_SOURCES:
            raise AffiliatePipelineError(f"unknown product video source: {source}")

        source_path = (
            state.character_wear_image if source == "worn" else state.isolated_product_image
        )
        if not source_path or not Path(source_path).is_file():
            raise AffiliatePipelineError(f"product video source is missing: {source}")

        provider_job_id = (
            f"{state.job_id}-product-{style}-{source}-a{state.product_video_attempt}"
        )
        try:
            ref = self.flow.generate_video(
                job_id=provider_job_id,
                prompt=prompt,
                image_path=source_path,
                duration_seconds=10,
                output_directory=str(
                    workspace / "clips" / f"product-{style}-{source}-a{state.product_video_attempt}"
                ),
                max_credit_cost=max_credit_per_video,
            )
            state.product_video = self._video_output(ref.provider_job_id)
            state.status = "PRODUCT_VIDEO_READY"
            self.job_store.clear_error(state)
        except Exception as exc:
            state.product_video_attempt += 1
            self.job_store.mark_error(state, "PRODUCT_VIDEO", str(exc))
            raise AffiliatePipelineError(
                "product video failed; inspect the failure before approving a paid retry"
            ) from exc

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
        overlay_image: Optional[str],
        overlay_position: str,
        overlay_width_pct: float,
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
        resolved_overlay = None
        if overlay_image:
            resolved_overlay = self._require_input(overlay_image, "overlay image")
        result = self.render.render_vertical(
            job_id=state.job_id,
            clip_paths=[state.character_video, state.product_video],
            voice_track=state.voice_audio,
            music_track=resolved_music,
            captions_ass=resolved_captions,
            overlay_image=resolved_overlay,
            overlay_position=overlay_position,
            overlay_width_pct=overlay_width_pct,
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
        extract_product_prompt: Optional[str] = None,
        wear_product_prompt: Optional[str] = None,
        character_video_prompt: Optional[str] = None,
        product_video_prompt: Optional[str] = None,
        voice_script: str = DEFAULT_VOICE_SCRIPT,
        voice: str = "Zephyr",
        product_video_style: str = "zoom",
        product_video_source: str = "worn",
        music_track: Optional[str] = None,
        captions_ass: Optional[str] = None,
        overlay_image: Optional[str] = None,
        overlay_position: str = "bottom-right",
        overlay_width_pct: float = 16.0,
        approve_video_credits: bool = False,
        approve_paid_retry: bool = False,
        max_credit_per_video: int = 15,
    ) -> dict:
        if product_video_style not in PRODUCT_VIDEO_PROMPTS:
            raise AffiliatePipelineError(
                f"unknown product video style: {product_video_style}"
            )
        if product_video_source not in PRODUCT_VIDEO_SOURCES:
            raise AffiliatePipelineError(
                f"unknown product video source: {product_video_source}"
            )
        if overlay_position not in OVERLAY_POSITIONS:
            raise AffiliatePipelineError(f"unknown overlay position: {overlay_position}")
        if not 1 <= float(overlay_width_pct) <= 50:
            raise AffiliatePipelineError("overlay width must be between 1 and 50 percent")

        existing = self.job_store.load(job_id)

        def effective_prompt(key: str, value: Optional[str], default: str, label: str) -> str:
            # When resuming an older job, omitted prompts mean "reuse what the job
            # already saved", not "upgrade to whatever the current code default is".
            if existing and value is None and existing.prompts.get(key):
                return existing.prompts[key]
            return self._resolve_prompt(value, default, label)

        prompts = {
            "extract_product": effective_prompt(
                "extract_product",
                extract_product_prompt,
                EXTRACT_PRODUCT_PROMPT,
                "extract product prompt",
            ),
            "wear_product": effective_prompt(
                "wear_product",
                wear_product_prompt,
                WEAR_PRODUCT_PROMPT,
                "wear product prompt",
            ),
            "character_video": effective_prompt(
                "character_video",
                character_video_prompt,
                character_video_attempt(3).prompt,
                "character video prompt",
            ),
            "product_video": effective_prompt(
                "product_video",
                product_video_prompt,
                PRODUCT_VIDEO_PROMPTS[product_video_style],
                "product video prompt",
            ),
        }
        state = self._load_or_create(
            job_id,
            character_image,
            product_image,
            prompts,
            product_video_source,
        )
        workspace = self._workspace(job_id)
        try:
            self._extract_product(
                state,
                workspace,
                prompt=prompts["extract_product"],
            )
            self._dress_character(
                state,
                workspace,
                prompt=prompts["wear_product"],
            )
            self._generate_character_video(
                state,
                workspace,
                primary_prompt=prompts["character_video"],
                approve_video_credits=approve_video_credits,
                approve_paid_retry=approve_paid_retry,
                max_credit_per_video=max_credit_per_video,
            )
            self._generate_product_video(
                state,
                workspace,
                style=product_video_style,
                prompt=prompts["product_video"],
                source=product_video_source,
                approve_video_credits=approve_video_credits,
                approve_paid_retry=approve_paid_retry,
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
                overlay_image=overlay_image,
                overlay_position=overlay_position,
                overlay_width_pct=overlay_width_pct,
            )
            return asdict(state)
        except Exception as exc:
            if state.error_message is None:
                self.job_store.mark_error(state, state.status, str(exc))
            raise
