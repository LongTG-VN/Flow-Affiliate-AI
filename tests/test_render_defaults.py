from flow_affiliate_ai.cli import build_parser
from flow_affiliate_ai.providers.render.base import RenderJobRef, ValidationResult
from flow_affiliate_ai.services.core import RenderService


class CaptureRenderProvider:
    def __init__(self) -> None:
        self.manifest = None

    def validate_inputs(self, manifest):
        return ValidationResult(True, [])

    def render(self, manifest):
        self.manifest = manifest
        return RenderJobRef(manifest.job_id, "COMPLETED", manifest.output_path, "sha")

    def probe(self, output):
        raise NotImplementedError


def test_render_service_defaults_match_dashboard_sticker_defaults():
    provider = CaptureRenderProvider()
    service = RenderService(provider)

    result = service.render_vertical(
        job_id="render-defaults",
        clip_paths=["character.mp4", "product.mp4"],
        output_path="final.mp4",
    )

    assert result.status == "COMPLETED"
    assert provider.manifest is not None
    assert provider.manifest.overlay_position == "bottom-right"
    assert provider.manifest.overlay_width_pct == 26.0
    assert provider.manifest.overlay_margin_px == 40


def test_cli_sticker_width_default_matches_dashboard():
    args = build_parser().parse_args(
        [
            "--job-id",
            "job-defaults",
            "--character",
            "character.png",
            "--product",
            "product.png",
        ]
    )

    assert args.sticker_position == "bottom-right"
    assert args.sticker_width_pct == 26.0
