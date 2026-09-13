import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from flow_affiliate_ai.dynamic_pipeline import DynamicAffiliatePipeline
from flow_affiliate_ai.providers.flow.gflow_cli import GFlowCliProvider
from flow_affiliate_ai.providers.planner.openai_planner import OpenAIShotPlanner
from flow_affiliate_ai.providers.render.ffmpeg import FfmpegRenderProvider
from flow_affiliate_ai.services.core import FlowService, RenderService


def build_factory_pipeline(*, data_root: Path) -> DynamicAffiliatePipeline:
    flow = FlowService(
        GFlowCliProvider(
            gflow_bin=os.getenv("GFLOW_BIN", "gflow"),
            profile=os.getenv("GFLOW_PROFILE") or None,
            state_dir=data_root / "gflow_jobs",
        )
    )
    planner = OpenAIShotPlanner(
        model=os.getenv("OPENAI_PLANNER_MODEL") or None,
        api_key=os.getenv("OPENAI_API_KEY") or None,
    )
    render = RenderService(
        FfmpegRenderProvider(
            ffmpeg_bin=os.getenv("FFMPEG_BIN", "ffmpeg"),
            ffprobe_bin=os.getenv("FFPROBE_BIN", "ffprobe"),
        )
    )
    return DynamicAffiliatePipeline(
        flow=flow,
        planner=planner,
        render=render,
        data_root=data_root,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flow-affiliate-factory",
        description=(
            "Generate a dynamic fashion affiliate video from one model image, "
            "one product image and one logo."
        ),
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--model", required=True, help="Original model reference image")
    parser.add_argument("--product", required=True, help="Product reference image")
    parser.add_argument("--logo", required=True, help="Logo image overlaid during final render")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--max-credit-per-video", type=int, default=15)
    parser.add_argument(
        "--approve-video-credits",
        action="store_true",
        help=(
            "Render the planned Flow clips. Without this flag the command stops "
            "after planning/dressing and prints the estimated total credits."
        ),
    )
    parser.add_argument(
        "--approve-paid-retry",
        action="store_true",
        help="Allow retry of a previously failed paid Flow shot.",
    )
    parser.add_argument(
        "--logo-position",
        choices=("top-left", "top-right", "bottom-left", "bottom-right", "center"),
        default="bottom-right",
    )
    parser.add_argument("--logo-width-pct", type=float, default=14.0)
    parser.add_argument("--logo-margin-px", type=int, default=40)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pipeline = build_factory_pipeline(data_root=Path(args.data_root))
    result = pipeline.run(
        job_id=args.job_id,
        model_image=args.model,
        product_image=args.product,
        logo_image=args.logo,
        approve_video_credits=args.approve_video_credits,
        approve_paid_retry=args.approve_paid_retry,
        max_credit_per_video=args.max_credit_per_video,
        overlay_position=args.logo_position,
        overlay_width_pct=args.logo_width_pct,
        overlay_margin_px=args.logo_margin_px,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
