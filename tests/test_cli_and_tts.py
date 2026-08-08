import wave
from pathlib import Path

from flow_affiliate_ai.cli import build_parser
from flow_affiliate_ai.providers.tts.base import TTSRequest
from flow_affiliate_ai.providers.tts.gemini_tts import GeminiTtsProvider


def test_cli_parses_full_v1_job_contract():
    args = build_parser().parse_args(
        [
            "--job-id",
            "dress-001",
            "--character",
            "character.png",
            "--product",
            "dress.png",
            "--tts",
            "edge",
            "--voice",
            "vi-VN-HoaiMyNeural",
            "--product-video-style",
            "pan",
            "--max-credit-per-video",
            "20",
            "--approve-video-credits",
            "--approve-paid-retry",
        ]
    )

    assert args.job_id == "dress-001"
    assert args.character == "character.png"
    assert args.product == "dress.png"
    assert args.tts == "edge"
    assert args.product_video_style == "pan"
    assert args.max_credit_per_video == 20
    assert args.approve_video_credits is True
    assert args.approve_paid_retry is True


def test_gemini_tts_prompt_preserves_script_and_creator_style():
    request = TTSRequest(
        job_id="tts-001",
        text="Mẫu này lên hình rất gọn.",
        voice="Zephyr",
        output_directory="data/audio",
        idempotency_key="key",
    )

    prompt = GeminiTtsProvider._prompt(request)
    assert "Mẫu này lên hình rất gọn." in prompt
    assert "creator TikTok thời trang" in prompt
    assert "Không thêm hoặc lược bỏ từ" in prompt


def test_gemini_pcm_writer_creates_valid_one_second_wav(tmp_path: Path):
    output = tmp_path / "voice.wav"
    pcm = b"\x00\x00" * 24000

    duration_ms = GeminiTtsProvider._write_wav(output, pcm)

    assert duration_ms == 1000
    with wave.open(str(output), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 24000
        assert handle.getnframes() == 24000
