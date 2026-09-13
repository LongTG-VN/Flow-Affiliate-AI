from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path

from flow_affiliate_ai.prompts.planner import PLANNER_SYSTEM_PROMPT, PLANNER_USER_PROMPT
from flow_affiliate_ai.shot_plan import ShotPlanV1, ShotPlanValidationError


class OpenAIPlannerError(RuntimeError):
    pass


def _image_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _clean_json_text(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


class OpenAIShotPlanner:
    """Generate a strict ShotPlanV1 from model + isolated-product images.

    The OpenAI dependency is intentionally optional so legacy V1 remains usable
    without installing planner extras.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAIPlannerError(
                'OpenAI planner dependency missing; install with pip install -e ".[planner]"'
            ) from exc
        self.model = model or os.getenv("OPENAI_PLANNER_MODEL", "gpt-5.6-luna")
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY") or None)

    def plan(
        self,
        *,
        model_image: Path,
        isolated_product_image: Path,
    ) -> tuple[ShotPlanV1, str]:
        model_image = model_image.expanduser().resolve()
        isolated_product_image = isolated_product_image.expanduser().resolve()
        if not model_image.is_file():
            raise OpenAIPlannerError(f"model image missing: {model_image}")
        if not isolated_product_image.is_file():
            raise OpenAIPlannerError(
                f"isolated product image missing: {isolated_product_image}"
            )

        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=PLANNER_SYSTEM_PROMPT,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": PLANNER_USER_PROMPT},
                            {
                                "type": "input_image",
                                "image_url": _image_data_url(model_image),
                            },
                            {
                                "type": "input_image",
                                "image_url": _image_data_url(isolated_product_image),
                            },
                        ],
                    }
                ],
            )
        except Exception as exc:
            raise OpenAIPlannerError(f"OpenAI planner request failed: {exc}") from exc

        raw = _clean_json_text(getattr(response, "output_text", "") or "")
        if not raw:
            raise OpenAIPlannerError("OpenAI planner returned no text output")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAIPlannerError(f"planner returned invalid JSON: {exc}") from exc
        try:
            plan = ShotPlanV1.from_dict(payload)
        except ShotPlanValidationError as exc:
            raise OpenAIPlannerError(f"planner JSON failed schema validation: {exc}") from exc
        return plan, raw
