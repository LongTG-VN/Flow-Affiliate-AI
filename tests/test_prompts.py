from flow_affiliate_ai.prompts.fashion import (
    CHARACTER_VIDEO_LEVELS,
    DEFAULT_VOICE_SCRIPT,
    character_video_attempt,
    fallback_level,
)


def test_character_video_levels_are_short_and_ordered():
    assert set(CHARACTER_VIDEO_LEVELS) == {1, 2, 3}
    assert len(CHARACTER_VIDEO_LEVELS[1]) < len(CHARACTER_VIDEO_LEVELS[3])


def test_fallback_levels_descend_without_auto_retry_loop():
    assert fallback_level(3) == 2
    assert fallback_level(2) == 1
    assert fallback_level(1) is None


def test_character_video_attempt_returns_requested_level():
    attempt = character_video_attempt(3)
    assert attempt.level == 3
    assert "vertical video" in attempt.prompt


def test_level_three_keeps_validated_single_step_motion():
    prompt = CHARACTER_VIDEO_LEVELS[3].lower()
    assert "one slow step forward" in prompt
    assert "two slow" not in prompt


def test_default_voice_script_avoids_unverified_material_claims():
    script = DEFAULT_VOICE_SCRIPT.lower()
    for phrase in ("mềm mại", "thoải mái", "co giãn", "bền", "chất liệu"):
        assert phrase not in script
