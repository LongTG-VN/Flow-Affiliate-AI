import hashlib
from pathlib import Path
from typing import Optional

from flow_affiliate_ai.providers.flow.base import (
    FlowGenerationRequest,
    FlowImageGenerationRequest,
    FlowProvider,
)
from flow_affiliate_ai.providers.render.base import ClipInput, RenderManifest, RenderProvider
from flow_affiliate_ai.providers.tts.base import TTSProvider, TTSRequest
from flow_affiliate_ai.prompts.fashion import character_video_attempt, fallback_level


class FlowService:
    def __init__(self, provider: FlowProvider) -> None:
        self.provider = provider

    def generate_image(
        self,
        *,
        job_id: str,
        prompt: str,
        reference_paths: list[str],
        output_path: str,
        aspect_ratio: str = "9:16",
        model: str = "nano2",
    ):
        if not reference_paths:
            raise ValueError("reference_paths cannot be empty")
        idempotency_key = hashlib.sha256(
            (
                f"{job_id}|{prompt}|{aspect_ratio}|{model}|"
                + "|".join(str(Path(p).resolve()) for p in reference_paths)
            ).encode("utf-8")
        ).hexdigest()
        request = FlowImageGenerationRequest(
            job_id=job_id,
            prompt=prompt,
            reference_paths=reference_paths,
            aspect_ratio=aspect_ratio,
            model=model,
            output_path=output_path,
            idempotency_key=idempotency_key,
        )
        return self.provider.generate_image(request)

    def generate_video(
        self,
        *,
        job_id: str,
        prompt: str,
        image_path: Optional[str] = None,
        duration_seconds: int = 10,
        output_directory: str,
        max_credit_cost: int = 15,
    ):
        idempotency_key = hashlib.sha256(
            f"{job_id}|{prompt}|{image_path}|{duration_seconds}".encode("utf-8")
        ).hexdigest()
        request = FlowGenerationRequest(
            job_id=job_id,
            prompt=prompt,
            duration_seconds=duration_seconds,
            output_directory=output_directory,
            max_credit_cost=max_credit_cost,
            idempotency_key=idempotency_key,
            reference_paths=[image_path] if image_path else [],
        )
        return self.provider.submit(request)

    @staticmethod
    def next_character_prompt(current_level: int):
        next_level = fallback_level(current_level)
        return character_video_attempt(next_level) if next_level else None


class VoiceService:
    def __init__(self, provider: TTSProvider) -> None:
        self.provider = provider

    def synthesize(
        self,
        *,
        job_id: str,
        text: str,
        output_directory: str,
        voice: str = "Zephyr",
    ):
        key = hashlib.sha256(f"{job_id}|{voice}|{text}".encode("utf-8")).hexdigest()
        return self.provider.synthesize(
            TTSRequest(
                job_id=job_id,
                text=text,
                voice=voice,
                output_directory=output_directory,
                idempotency_key=key,
            )
        )


class RenderService:
    def __init__(self, provider: RenderProvider) -> None:
        self.provider = provider

    def render_vertical(
        self,
        *,
        job_id: str,
        clip_paths: list[str],
        output_path: str,
        voice_track: Optional[str] = None,
        music_track: Optional[str] = None,
        captions_ass: Optional[str] = None,
        overlay_image: Optional[str] = None,
        overlay_position: str = "bottom-right",
        overlay_width_pct: float = 26.0,
        overlay_margin_px: int = 40,
    ):
        clips = [
            ClipInput(clip_id=f"clip-{index + 1}", path=path)
            for index, path in enumerate(clip_paths)
        ]
        return self.provider.render(
            RenderManifest(
                job_id=job_id,
                clips=clips,
                voice_track=voice_track,
                music_track=music_track,
                captions_ass=captions_ass,
                overlay_image=overlay_image,
                overlay_position=overlay_position,
                overlay_width_pct=overlay_width_pct,
                overlay_margin_px=overlay_margin_px,
                output_path=output_path,
            )
        )
