from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


ALLOWED_DURATIONS = {4, 6, 8, 10}
ALLOWED_SOURCE_TYPES = {"worn_model", "isolated_product"}
ALLOWED_PURPOSES = {"hook", "showcase", "detail", "lifestyle", "beauty", "ending"}
MIN_SHOTS = 4
MAX_SHOTS = 8


class ShotPlanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ShotSpec:
    shot_id: str
    purpose: str
    source_type: str
    duration_seconds: int
    flow_prompt: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShotSpec":
        try:
            shot = cls(
                shot_id=str(data["shot_id"]).strip(),
                purpose=str(data["purpose"]).strip(),
                source_type=str(data["source_type"]).strip(),
                duration_seconds=int(data["duration_seconds"]),
                flow_prompt=str(data["flow_prompt"]).strip(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ShotPlanValidationError(f"invalid shot object: {exc}") from exc
        shot.validate()
        return shot

    def validate(self) -> None:
        if not self.shot_id:
            raise ShotPlanValidationError("shot_id cannot be empty")
        if self.purpose not in ALLOWED_PURPOSES:
            raise ShotPlanValidationError(f"invalid purpose for {self.shot_id}: {self.purpose}")
        if self.source_type not in ALLOWED_SOURCE_TYPES:
            raise ShotPlanValidationError(
                f"invalid source_type for {self.shot_id}: {self.source_type}"
            )
        if self.duration_seconds not in ALLOWED_DURATIONS:
            raise ShotPlanValidationError(
                f"invalid duration for {self.shot_id}: {self.duration_seconds}"
            )
        if not self.flow_prompt:
            raise ShotPlanValidationError(f"flow_prompt cannot be empty: {self.shot_id}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShotPlanV1:
    schema_version: str
    project_type: str
    creative_summary: str
    wear_model_prompt: str
    total_shots: int
    shots: tuple[ShotSpec, ...]
    edit_sequence: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShotPlanV1":
        if not isinstance(data, dict):
            raise ShotPlanValidationError("planner output must be a JSON object")
        try:
            raw_shots = data["shots"]
            raw_sequence = data["edit_sequence"]
            if not isinstance(raw_shots, list):
                raise ShotPlanValidationError("shots must be an array")
            if not isinstance(raw_sequence, list):
                raise ShotPlanValidationError("edit_sequence must be an array")
            plan = cls(
                schema_version=str(data["schema_version"]).strip(),
                project_type=str(data["project_type"]).strip(),
                creative_summary=str(data.get("creative_summary", "")).strip(),
                wear_model_prompt=str(data["wear_model_prompt"]).strip(),
                total_shots=int(data["total_shots"]),
                shots=tuple(ShotSpec.from_dict(item) for item in raw_shots),
                edit_sequence=tuple(str(item).strip() for item in raw_sequence),
            )
        except ShotPlanValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ShotPlanValidationError(f"invalid shot plan: {exc}") from exc
        plan.validate()
        return plan

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ShotPlanValidationError("schema_version must be 1.0")
        if self.project_type != "fashion_affiliate":
            raise ShotPlanValidationError("project_type must be fashion_affiliate")
        if not self.wear_model_prompt:
            raise ShotPlanValidationError("wear_model_prompt cannot be empty")
        if not MIN_SHOTS <= self.total_shots <= MAX_SHOTS:
            raise ShotPlanValidationError(
                f"total_shots must be between {MIN_SHOTS} and {MAX_SHOTS}"
            )
        if self.total_shots != len(self.shots):
            raise ShotPlanValidationError("total_shots must equal shots length")

        expected_ids = tuple(f"shot_{index:02d}" for index in range(1, self.total_shots + 1))
        actual_ids = tuple(shot.shot_id for shot in self.shots)
        if actual_ids != expected_ids:
            raise ShotPlanValidationError(
                f"shot ids must be sequential: expected {expected_ids}, got {actual_ids}"
            )
        if len(set(actual_ids)) != len(actual_ids):
            raise ShotPlanValidationError("shot ids must be unique")
        if len(self.edit_sequence) != self.total_shots:
            raise ShotPlanValidationError("edit_sequence length must equal total_shots")
        if len(set(self.edit_sequence)) != len(self.edit_sequence):
            raise ShotPlanValidationError("edit_sequence must not repeat shot ids")
        if set(self.edit_sequence) != set(actual_ids):
            raise ShotPlanValidationError(
                "edit_sequence must contain every generated shot exactly once"
            )

    def by_id(self) -> dict[str, ShotSpec]:
        return {shot.shot_id: shot for shot in self.shots}

    def ordered_shots(self) -> tuple[ShotSpec, ...]:
        lookup = self.by_id()
        return tuple(lookup[shot_id] for shot_id in self.edit_sequence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_type": self.project_type,
            "creative_summary": self.creative_summary,
            "wear_model_prompt": self.wear_model_prompt,
            "total_shots": self.total_shots,
            "shots": [shot.to_dict() for shot in self.shots],
            "edit_sequence": list(self.edit_sequence),
        }
