import subprocess
from pathlib import Path

import pytest

from flow_affiliate_ai.providers.flow.base import (
    FlowGenerationRequest,
    FlowImageGenerationRequest,
)
from flow_affiliate_ai.providers.flow.gflow_cli import (
    GFlowCliProvider,
    GFlowCliProviderError,
)


def test_image_i2i_builds_expected_gflow_command(tmp_path, monkeypatch):
    ref_a = tmp_path / "character.png"
    ref_b = tmp_path / "dress.png"
    ref_a.write_bytes(b"a")
    ref_b.write_bytes(b"b")
    output = tmp_path / "out" / "wear.png"

    provider = GFlowCliProvider(state_dir=tmp_path / "state")
    monkeypatch.setattr(provider, "_ensure_ready", lambda: "gflow")
    captured = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        target_dir = Path(command[command.index("--out") + 1])
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "generated.png").write_bytes(b"generated")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = provider.generate_image(
        FlowImageGenerationRequest(
            job_id="image-001",
            prompt="replace the outfit",
            reference_paths=[str(ref_a), str(ref_b)],
            aspect_ratio="9:16",
            model="nano2",
            output_path=str(output),
            idempotency_key="image-key",
        )
    )

    command = captured["command"]
    assert command[:3] == ["gflow", "image", "i2i"]
    assert command[3] == "replace the outfit"
    assert command.count("--ref") == 2
    assert str(ref_a.resolve()) in command
    assert str(ref_b.resolve()) in command
    assert command[command.index("--aspect") + 1] == "9:16"
    assert command[command.index("--model") + 1] == "nano2"
    assert command[command.index("--count") + 1] == "2"
    out_dir = Path(command[command.index("--out") + 1])
    assert out_dir.name == "image-001"
    assert out_dir.parent.name == ".gflow"
    assert Path(result.output_path).read_bytes() == b"generated"


def test_reference_video_builds_expected_r2v_command(tmp_path, monkeypatch):
    reference = tmp_path / "character.png"
    reference.write_bytes(b"image")
    output_dir = tmp_path / "clips"

    provider = GFlowCliProvider(state_dir=tmp_path / "state")
    monkeypatch.setattr(provider, "_ensure_ready", lambda: "gflow")
    captured = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        out = Path(command[command.index("--out-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "generated.mp4").write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ref = provider.submit(
        FlowGenerationRequest(
            job_id="video-001",
            prompt="The woman slowly turns toward the camera.",
            duration_seconds=10,
            aspect_ratio="9:16",
            max_credit_cost=15,
            idempotency_key="video-key",
            output_directory=str(output_dir),
            reference_paths=[str(reference)],
        )
    )

    command = captured["command"]
    assert command[:3] == ["gflow", "video", "r2v"]
    assert command.count("--ref") == 1
    assert command[command.index("--duration") + 1] == "10"
    assert command[command.index("--aspect") + 1] == "9:16"
    assert ref.status == "COMPLETED"
    status = provider.poll(ref.provider_job_id)
    assert status.status == "COMPLETED"
    assert len(status.output_paths) == 1


def test_video_credit_ceiling_blocks_submission(tmp_path, monkeypatch):
    provider = GFlowCliProvider(state_dir=tmp_path / "state")
    monkeypatch.setattr(provider, "_ensure_ready", lambda: "gflow")

    with pytest.raises(GFlowCliProviderError, match="credit ceiling exceeded"):
        provider.submit(
            FlowGenerationRequest(
                job_id="video-expensive",
                prompt="test",
                duration_seconds=10,
                max_credit_cost=0,
                idempotency_key="key",
                output_directory=str(tmp_path / "clips"),
            )
        )


def test_duplicate_completed_image_is_idempotent(tmp_path, monkeypatch):
    reference = tmp_path / "dress.png"
    reference.write_bytes(b"image")
    output = tmp_path / "isolated.png"
    provider = GFlowCliProvider(state_dir=tmp_path / "state")
    monkeypatch.setattr(provider, "_ensure_ready", lambda: "gflow")
    calls = 0

    def fake_run(command, **_kwargs):
        nonlocal calls
        calls += 1
        target_dir = Path(command[command.index("--out") + 1])
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "generated.png").write_bytes(b"generated")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    request = FlowImageGenerationRequest(
        job_id="image-idempotent",
        prompt="extract product",
        reference_paths=[str(reference)],
        output_path=str(output),
        idempotency_key="same-key",
    )

    first = provider.generate_image(request)
    second = provider.generate_image(request)

    assert first.output_path == second.output_path
    assert calls == 1


def test_image_generation_never_reuses_stale_image_from_stage_directory(tmp_path, monkeypatch):
    reference = tmp_path / "dress.png"
    reference.write_bytes(b"image")
    output_dir = tmp_path / "images"
    output_dir.mkdir()
    stale = output_dir / "old-stage.png"
    stale.write_bytes(b"stale")

    provider = GFlowCliProvider(state_dir=tmp_path / "state")
    monkeypatch.setattr(provider, "_ensure_ready", lambda: "gflow")

    def fake_run(command, **_kwargs):
        # Simulate gflow returning success but producing no image in this attempt.
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GFlowCliProviderError, match="output file was not produced"):
        provider.generate_image(
            FlowImageGenerationRequest(
                job_id="image-stale-guard",
                prompt="extract product",
                reference_paths=[str(reference)],
                output_path=str(output_dir / "isolated.png"),
                idempotency_key="stale-key",
            )
        )

    assert stale.read_bytes() == b"stale"
    assert not (output_dir / "isolated.png").exists()


def test_image_generation_preserves_generated_jpeg_extension(tmp_path, monkeypatch):
    reference = tmp_path / "dress.png"
    reference.write_bytes(b"image")
    output = tmp_path / "images" / "isolated.png"
    provider = GFlowCliProvider(state_dir=tmp_path / "state")
    monkeypatch.setattr(provider, "_ensure_ready", lambda: "gflow")

    def fake_run(command, **_kwargs):
        target_dir = Path(command[command.index("--out") + 1])
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "generated.jpg").write_bytes(b"jpeg-bytes")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = provider.generate_image(
        FlowImageGenerationRequest(
            job_id="image-jpeg",
            prompt="extract product",
            reference_paths=[str(reference)],
            output_path=str(output),
            idempotency_key="jpeg-key",
        )
    )

    resolved = Path(result.output_path)
    assert resolved.suffix == ".jpg"
    assert resolved.read_bytes() == b"jpeg-bytes"
    assert not output.exists()
