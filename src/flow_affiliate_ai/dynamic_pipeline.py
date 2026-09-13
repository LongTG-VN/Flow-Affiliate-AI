from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from flow_affiliate_ai.jobs import AffiliateJobState, JobStore
from flow_affiliate_ai.prompts.fashion import EXTRACT_PRODUCT_PROMPT
from flow_affiliate_ai.providers.planner.base import PlannerProvider
from flow_affiliate_ai.services.core import FlowService, RenderService
from flow_affiliate_ai.shot_plan import ShotPlanV1


OVERLAY_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}


class DynamicFactoryError(RuntimeError):
    pass


class PaidShotRetryApprovalRequired(DynamicFactoryError):
    def __init__(self, job_id: str, shot_id: str) -> None:
        self.job_id = job_id
        self.shot_id = shot_id
        super().__init__(
            f"{shot_id} previously failed; retry requires explicit paid-retry approval"
        )


class DynamicAffiliatePipeline:
    """Product + model + logo -> plan -> N Flow clips -> final video.

    This pipeline intentionally lives beside the legacy AffiliatePipeline. It reuses
    the same FlowService, RenderService and JobStore while keeping the old two-video
    contract untouched.
    """

    def __init__(
        self,
        *,
        flow: FlowService,
        planner: PlannerProvider,
        render: RenderService,
        data_root: Path = Path("data"),
        job_store: Optional[JobStore] = None,
    ) -> None:
        self.flow = flow
        self.planner = planner
        self.render = render
        self.data_root = data_root.resolve()
        self.job_store = job_store or JobStore(self.data_root / "jobs")

    def _workspace(self, job_id: str) -> Path:
        root = self.data_root / "runs" / job_id
        for child in ("images", "planner", "clips", "renders"):
            (root / child).mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _require_input(path: str, label: str) -> str:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise DynamicFactoryError(f"{label} does not exist: {resolved}")
        return str(resolved)

    def _load_or_create(
        self,
        *,
        job_id: str,
        model_image: str,
        product_image: str,
        logo_image: str,
    ) -> AffiliateJobState:
        model = self._require_input(model_image, "model image")
        product = self._require_input(product_image, "product image")
        logo = self._require_input(logo_image, "logo image")
        existing = self.job_store.load(job_id)
        if existing is not None:
            if Path(existing.character_image).resolve() != Path(model).resolve():
                raise DynamicFactoryError("job model image cannot be changed")
            if Path(existing.product_image).resolve() != Path(product).resolve():
                raise DynamicFactoryError("job product image cannot be changed")
            if existing.logo_image is None:
                if existing.shot_plan or existing.character_video or existing.product_video:
                    raise DynamicFactoryError(
                        "job id belongs to a legacy or already-started job without a logo"
                    )
                existing.logo_image = logo
                self.job_store.save(existing)
            elif Path(existing.logo_image).resolve() != Path(logo).resolve():
                raise DynamicFactoryError("job logo image cannot be changed")
            return existing

        state = AffiliateJobState(
            job_id=job_id,
            character_image=model,
            product_image=product,
            logo_image=logo,
        )
        self.job_store.save(state)
        return state

    def _extract_product(self, state: AffiliateJobState, workspace: Path) -> None:
        if state.isolated_product_image and Path(state.isolated_product_image).is_file():
            return
        provider_job_id = f"{state.job_id}-extract-product-a{state.extract_attempt}"
        try:
            result = self.flow.generate_image(
                job_id=provider_job_id,
                prompt=EXTRACT_PRODUCT_PROMPT,
                reference_paths=[state.product_image],
                output_path=str(workspace / "images" / "product_isolated.png"),
                aspect_ratio="9:16",
            )
            if result.status != "COMPLETED" or not result.output_path:
                raise DynamicFactoryError(result.error_message or "product extraction failed")
            state.isolated_product_image = result.output_path
            state.status = "PRODUCT_EXTRACTED"
            self.job_store.clear_error(state)
        except Exception as exc:
            state.extract_attempt += 1
            self.job_store.mark_error(state, "PRODUCT_EXTRACTION", str(exc))
            raise

    def _load_plan_from_state(self, state: AffiliateJobState) -> Optional[ShotPlanV1]:
        if not state.shot_plan:
            return None
        return ShotPlanV1.from_dict(state.shot_plan)

    def _plan(self, state: AffiliateJobState, workspace: Path) -> ShotPlanV1:
        existing = self._load_plan_from_state(state)
        if existing is not None:
            return existing
        if not state.isolated_product_image:
            raise DynamicFactoryError("isolated product image is missing before planning")

        state.status = "PLANNING"
        self.job_store.clear_error(state)
        try:
            plan, raw = self.planner.plan(
                model_image=Path(state.character_image),
                isolated_product_image=Path(state.isolated_product_image),
            )
            plan.validate()
            plan_path = workspace / "planner" / "plan.json"
            raw_path = workspace / "planner" / "raw_response.json"
            plan_path.write_text(
                json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raw_path.write_text(raw, encoding="utf-8")
            state.shot_plan = plan.to_dict()
            state.shot_plan_raw = str(raw_path.resolve())
            state.metadata["shot_plan_path"] = str(plan_path.resolve())
            state.status = "PLAN_READY"
            self.job_store.clear_error(state)
            return plan
        except Exception as exc:
            state.planner_attempt += 1
            self.job_store.mark_error(state, "PLANNING", str(exc))
            raise

    def _dress_model(
        self,
        state: AffiliateJobState,
        workspace: Path,
        plan: ShotPlanV1,
    ) -> None:
        current = state.canonical_worn_image or state.character_wear_image
        if current and Path(current).is_file():
            state.canonical_worn_image = current
            state.character_wear_image = current
            return
        if not state.isolated_product_image:
            raise DynamicFactoryError("isolated product image is missing")

        provider_job_id = f"{state.job_id}-dress-model-a{state.dress_attempt}"
        try:
            result = self.flow.generate_image(
                job_id=provider_job_id,
                prompt=plan.wear_model_prompt,
                reference_paths=[state.character_image, state.isolated_product_image],
                output_path=str(workspace / "images" / "model_wearing_product.png"),
                aspect_ratio="9:16",
            )
            if result.status != "COMPLETED" or not result.output_path:
                raise DynamicFactoryError(result.error_message or "model dressing failed")
            state.canonical_worn_image = result.output_path
            # Alias legacy field so the current dashboard can preview this image.
            state.character_wear_image = result.output_path
            state.status = "CHARACTER_DRESSED"
            self.job_store.clear_error(state)
        except Exception as exc:
            state.dress_attempt += 1
            self.job_store.mark_error(state, "MODEL_DRESSING", str(exc))
            raise

    def _source_path(self, state: AffiliateJobState, source_type: str) -> str:
        if source_type == "worn_model":
            path = state.canonical_worn_image or state.character_wear_image
        elif source_type == "isolated_product":
            path = state.isolated_product_image
        else:
            raise DynamicFactoryError(f"unknown shot source_type: {source_type}")
        if not path or not Path(path).is_file():
            raise DynamicFactoryError(f"shot source is missing: {source_type}")
        return path

    def _estimate_credits(
        self,
        state: AffiliateJobState,
        workspace: Path,
        plan: ShotPlanV1,
        *,
        max_credit_per_video: int,
    ) -> int:
        estimates: dict[str, int] = {}
        total = 0
        for shot in plan.shots:
            source = self._source_path(state, shot.source_type)
            quote = self.flow.estimate_video(
                job_id=f"{state.job_id}-{shot.shot_id}-estimate",
                prompt=shot.flow_prompt,
                image_path=source,
                duration_seconds=shot.duration_seconds,
                output_directory=str(workspace / "clips" / shot.shot_id),
                max_credit_cost=max_credit_per_video,
            )
            if not quote.can_proceed:
                raise DynamicFactoryError(
                    f"credit ceiling exceeded for {shot.shot_id}: "
                    f"quoted {quote.quoted_credit_cost}, ceiling {quote.max_credit_cost}"
                )
            estimates[shot.shot_id] = int(quote.quoted_credit_cost)
            total += int(quote.quoted_credit_cost)
        state.metadata["shot_credit_estimates"] = estimates
        state.metadata["estimated_total_credits"] = total
        state.metadata["total_shots"] = plan.total_shots
        self.job_store.save(state)
        return total

    @staticmethod
    def _completed_shot_path(result: dict) -> Optional[str]:
        if result.get("status") != "COMPLETED":
            return None
        path = result.get("output_path")
        if path and Path(path).is_file():
            return str(Path(path).resolve())
        return None

    def _video_output(self, provider_job_id: str) -> str:
        status = self.flow.provider.poll(provider_job_id)
        if status.status != "COMPLETED" or not status.output_paths:
            raise DynamicFactoryError(
                status.error_message or f"Flow video has no output: {provider_job_id}"
            )
        output = Path(status.output_paths[0]).resolve()
        if not output.is_file():
            raise DynamicFactoryError(f"Flow video output missing: {output}")
        return str(output)

    def _generate_shots(
        self,
        state: AffiliateJobState,
        workspace: Path,
        plan: ShotPlanV1,
        *,
        approve_paid_retry: bool,
        max_credit_per_video: int,
    ) -> None:
        state.status = "SHOT_RENDERING"
        self.job_store.clear_error(state)

        for shot in plan.shots:
            previous = dict(state.shot_results.get(shot.shot_id, {}))
            completed = self._completed_shot_path(previous)
            if completed:
                continue

            previous_status = previous.get("status")
            if previous_status == "FAILED" and not approve_paid_retry:
                raise PaidShotRetryApprovalRequired(state.job_id, shot.shot_id)

            attempt = int(previous.get("attempt", 0) or 0) + 1
            provider_job_id = f"{state.job_id}-{shot.shot_id}-a{attempt}"
            source = self._source_path(state, shot.source_type)
            state.shot_results[shot.shot_id] = {
                "status": "RUNNING",
                "source_type": shot.source_type,
                "duration_seconds": shot.duration_seconds,
                "prompt": shot.flow_prompt,
                "provider_job_id": provider_job_id,
                "output_path": None,
                "attempt": attempt,
                "error_message": None,
            }
            self.job_store.save(state)

            try:
                ref = self.flow.generate_video(
                    job_id=provider_job_id,
                    prompt=shot.flow_prompt,
                    image_path=source,
                    duration_seconds=shot.duration_seconds,
                    output_directory=str(workspace / "clips" / shot.shot_id / f"a{attempt}"),
                    max_credit_cost=max_credit_per_video,
                )
                output = self._video_output(ref.provider_job_id)
                state.shot_results[shot.shot_id].update(
                    status="COMPLETED",
                    output_path=output,
                    quoted_credit_cost=int(ref.quoted_credit_cost),
                )
                self.job_store.save(state)
            except Exception as exc:
                state.shot_results[shot.shot_id].update(
                    status="FAILED",
                    error_message=str(exc),
                )
                self.job_store.mark_error(state, f"SHOT_RENDERING:{shot.shot_id}", str(exc))
                raise

        state.status = "SHOTS_READY"
        self.job_store.clear_error(state)

    def _render_final(
        self,
        state: AffiliateJobState,
        workspace: Path,
        plan: ShotPlanV1,
        *,
        overlay_position: str,
        overlay_width_pct: float,
        overlay_margin_px: int,
    ) -> None:
        if state.final_video and Path(state.final_video).is_file():
            return
        if not state.logo_image or not Path(state.logo_image).is_file():
            raise DynamicFactoryError("logo image is missing before final render")

        clip_paths: list[str] = []
        for shot_id in plan.edit_sequence:
            result = state.shot_results.get(shot_id, {})
            path = self._completed_shot_path(result)
            if not path:
                raise DynamicFactoryError(f"shot is not completed: {shot_id}")
            clip_paths.append(path)

        state.status = "FINAL_RENDERING"
        self.job_store.clear_error(state)
        result = self.render.render_vertical(
            job_id=state.job_id,
            clip_paths=clip_paths,
            overlay_image=state.logo_image,
            overlay_position=overlay_position,
            overlay_width_pct=overlay_width_pct,
            overlay_margin_px=overlay_margin_px,
            output_path=str(workspace / "renders" / "final_video.mp4"),
        )
        if result.status != "COMPLETED" or not result.master_output_path:
            self.job_store.mark_error(
                state,
                "FINAL_RENDERING",
                result.error_message or "final render failed",
            )
            raise DynamicFactoryError(result.error_message or "final render failed")
        state.final_video = result.master_output_path
        state.status = "COMPLETED"
        self.job_store.clear_error(state)

    def run(
        self,
        *,
        job_id: str,
        model_image: str,
        product_image: str,
        logo_image: str,
        approve_video_credits: bool = False,
        approve_paid_retry: bool = False,
        max_credit_per_video: int = 15,
        overlay_position: str = "bottom-right",
        overlay_width_pct: float = 14.0,
        overlay_margin_px: int = 40,
    ) -> dict:
        if overlay_position not in OVERLAY_POSITIONS:
            raise DynamicFactoryError(f"unknown overlay position: {overlay_position}")
        if not 1 <= float(overlay_width_pct) <= 50:
            raise DynamicFactoryError("overlay width must be between 1 and 50 percent")
        if overlay_margin_px < 0:
            raise DynamicFactoryError("overlay margin cannot be negative")
        if max_credit_per_video < 0:
            raise DynamicFactoryError("max_credit_per_video cannot be negative")

        state = self._load_or_create(
            job_id=job_id,
            model_image=model_image,
            product_image=product_image,
            logo_image=logo_image,
        )
        workspace = self._workspace(job_id)

        try:
            self._extract_product(state, workspace)
            plan = self._plan(state, workspace)
            self._dress_model(state, workspace, plan)
            self._estimate_credits(
                state,
                workspace,
                plan,
                max_credit_per_video=max_credit_per_video,
            )

            if not approve_video_credits:
                state.status = "PLAN_READY"
                self.job_store.clear_error(state)
                return asdict(state)

            self._generate_shots(
                state,
                workspace,
                plan,
                approve_paid_retry=approve_paid_retry,
                max_credit_per_video=max_credit_per_video,
            )
            self._render_final(
                state,
                workspace,
                plan,
                overlay_position=overlay_position,
                overlay_width_pct=overlay_width_pct,
                overlay_margin_px=overlay_margin_px,
            )
            return asdict(state)
        except Exception as exc:
            if state.error_message is None:
                self.job_store.mark_error(state, state.status, str(exc))
            raise
