from __future__ import annotations

from pathlib import Path
from typing import Protocol

from flow_affiliate_ai.shot_plan import ShotPlanV1


class PlannerProvider(Protocol):
    def plan(
        self,
        *,
        model_image: Path,
        isolated_product_image: Path,
    ) -> tuple[ShotPlanV1, str]: ...
