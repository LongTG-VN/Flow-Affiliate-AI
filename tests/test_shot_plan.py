import pytest

from flow_affiliate_ai.shot_plan import ShotPlanV1, ShotPlanValidationError


def _payload():
    return {
        "schema_version": "1.0",
        "project_type": "fashion_affiliate",
        "creative_summary": "clean dress commercial",
        "wear_model_prompt": "Dress the same model in the exact referenced garment.",
        "total_shots": 4,
        "shots": [
            {
                "shot_id": "shot_01",
                "purpose": "hook",
                "source_type": "worn_model",
                "duration_seconds": 4,
                "flow_prompt": "Slow push-in on the same model wearing the exact product.",
            },
            {
                "shot_id": "shot_02",
                "purpose": "showcase",
                "source_type": "worn_model",
                "duration_seconds": 6,
                "flow_prompt": "One slow step forward, full-body fashion showcase.",
            },
            {
                "shot_id": "shot_03",
                "purpose": "detail",
                "source_type": "isolated_product",
                "duration_seconds": 4,
                "flow_prompt": "Slow product detail pan with exact garment fidelity.",
            },
            {
                "shot_id": "shot_04",
                "purpose": "ending",
                "source_type": "worn_model",
                "duration_seconds": 4,
                "flow_prompt": "Subtle final beauty pose with clean negative space.",
            },
        ],
        "edit_sequence": ["shot_01", "shot_02", "shot_03", "shot_04"],
    }


def test_valid_plan_round_trips():
    plan = ShotPlanV1.from_dict(_payload())
    assert plan.total_shots == 4
    assert plan.to_dict() == _payload()


def test_rejects_total_shot_mismatch():
    payload = _payload()
    payload["total_shots"] = 5
    with pytest.raises(ShotPlanValidationError, match="total_shots"):
        ShotPlanV1.from_dict(payload)


def test_rejects_non_sequential_ids():
    payload = _payload()
    payload["shots"][2]["shot_id"] = "shot_04"
    with pytest.raises(ShotPlanValidationError, match="sequential"):
        ShotPlanV1.from_dict(payload)


def test_rejects_invalid_duration():
    payload = _payload()
    payload["shots"][0]["duration_seconds"] = 5
    with pytest.raises(ShotPlanValidationError, match="invalid duration"):
        ShotPlanV1.from_dict(payload)


def test_rejects_invalid_source_type():
    payload = _payload()
    payload["shots"][0]["source_type"] = "random_image"
    with pytest.raises(ShotPlanValidationError, match="invalid source_type"):
        ShotPlanV1.from_dict(payload)


def test_rejects_edit_sequence_duplicates_or_omissions():
    payload = _payload()
    payload["edit_sequence"] = ["shot_01", "shot_02", "shot_02", "shot_04"]
    with pytest.raises(ShotPlanValidationError, match="must not repeat"):
        ShotPlanV1.from_dict(payload)
