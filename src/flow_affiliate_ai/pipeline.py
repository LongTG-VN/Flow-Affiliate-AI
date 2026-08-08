from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flow_affiliate_ai.prompts.fashion import PRODUCT_VIDEO_PROMPTS, character_video_attempt
from flow_affiliate_ai.services.core import FlowService, RenderService, VoiceService


@dataclass(frozen=True)
class AffiliateRun:
    job_id: str
    character_image: str
    product_image: str
    character_clip: Optional[str] = None
    product_clip: Optional[str] = None
    voice_track: Optional[str] = None
    final_video: Optional[str] = None


class AffiliatePipeline:
    """Thin orchestration layer over the three reusable cores.

    Image creation/product isolation remains a separate upstream step for now.
    This class starts once the final character-wearing-product image and clean
    product image are available.
    """

    def __init__(
        self,
        flow: FlowService,
        voice: VoiceService,
        render: RenderService,
        data_root: Path = Path("data"),
    ) -> None:
        self.flow = flow
        self.voice = voice
        self.render = render
        self.data_root = data_root

    def generate_character_clip(self, job_id: str, image_path: str, level: int = 3):
        attempt = character_video_attempt(level)
        return self.flow.generate_video(
            job_id=f"{job_id}-character-l{level}",
            prompt=attempt.prompt,
            image_path=image_path,
            duration_seconds=10,
            output_directory=str(self.data_root / "clips" / job_id / "character"),
        )

    def generate_product_clip(self, job_id: str, image_path: str, style: str = "zoom"):
        if style not in PRODUCT_VIDEO_PROMPTS:
            raise ValueError(f"unknown product video style: {style}")
        return self.flow.generate_video(
            job_id=f"{job_id}-product-{style}",
            prompt=PRODUCT_VIDEO_PROMPTS[style],
            image_path=image_path,
            duration_seconds=10,
            output_directory=str(self.data_root / "clips" / job_id / "product"),
        )

    def synthesize_voice(self, job_id: str, script: str, voice: str = "Zephyr"):
        return self.voice.synthesize(
            job_id=f"{job_id}-voice",
            text=script,
            voice=voice,
            output_directory=str(self.data_root / "audio" / job_id),
        )

    def render_final(
        self,
        job_id: str,
        clip_paths: list[str],
        voice_track: Optional[str] = None,
        music_track: Optional[str] = None,
        captions_ass: Optional[str] = None,
    ):
        return self.render.render_vertical(
            job_id=job_id,
            clip_paths=clip_paths,
            voice_track=voice_track,
            music_track=music_track,
            captions_ass=captions_ass,
            output_path=str(self.data_root / "renders" / f"{job_id}.mp4"),
        )
