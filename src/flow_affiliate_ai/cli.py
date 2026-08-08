import argparse
import json
import os
from pathlib import Path

from flow_affiliate_ai.pipeline import AffiliatePipeline
from flow_affiliate_ai.providers.flow.gflow_cli import GFlowCliProvider
from flow_affiliate_ai.providers.render.ffmpeg import FfmpegRenderProvider
from flow_affiliate_ai.providers.tts.edge_tts import EdgeTtsProvider
from flow_affiliate_ai.providers.tts.gemini_tts import GeminiTtsProvider
from flow_affiliate_ai.services.core import FlowService, RenderService, VoiceService


def build_pipeline(*, tts_provider: str, data_root: Path) -> AffiliatePipeline:
    flow = FlowService(
        GFlowCliProvider(
            gflow_bin=os.getenv("GFLOW_BIN", "gflow"),
            profile=os.getenv("GFLOW_PROFILE") or None,
            state_dir=data_root / "gflow_jobs",
        )
    )
    if tts_provider == "edge":
        tts = EdgeTtsProvider()
    else:
        tts = GeminiTtsProvider()
    voice = VoiceService(tts)
    render = RenderService(FfmpegRenderProvider())
    return AffiliatePipeline(
        flow=flow,
        voice=voice,
        render=render,
        data_root=data_root,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flow-affiliate",
        description=(
            "Generate a complete vertical fashion affiliate video from one "
            "character image and one product image using fixed prompts."
        ),
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--character", required=True, help="Character reference image")
    parser.add_argument("--product", required=True, help="Product reference image")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--tts", choices=("gemini", "edge"), default="gemini")
    parser.add_argument("--voice", default="Zephyr")
    parser.add_argument("--product-video-style", choices=("zoom", "pan"), default="zoom")
    parser.add_argument("--music", default=None)
    parser.add_argument("--captions-ass", default=None)
    parser.add_argument("--max-credit-per-video", type=int, default=15)
    parser.add_argument(
        "--approve-video-credits",
        action="store_true",
        help="Explicitly approve the first paid Flow video attempts.",
    )
    parser.add_argument(
        "--approve-paid-retry",
        action="store_true",
        help="Explicitly approve a paid fallback retry after a failed character-video attempt.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pipeline = build_pipeline(
        tts_provider=args.tts,
        data_root=Path(args.data_root),
    )
    result = pipeline.run(
        job_id=args.job_id,
        character_image=args.character,
        product_image=args.product,
        voice=args.voice,
        product_video_style=args.product_video_style,
        music_track=args.music,
        captions_ass=args.captions_ass,
        approve_video_credits=args.approve_video_credits,
        approve_paid_retry=args.approve_paid_retry,
        max_credit_per_video=args.max_credit_per_video,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
