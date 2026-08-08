import hashlib
import json
import os
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .base import (
    CostQuote,
    FlowGenerationRequest,
    FlowHealth,
    FlowImageGenerationRequest,
    FlowImageResult,
    ProviderCapabilities,
    ProviderJobRef,
    ProviderJobStatus,
)


FLOW_ALLOWED_DURATIONS = (4, 6, 8, 10)
FLOW_DEFAULT_MODEL = "omni-flash"
FLOW_OUTPUT_COUNT = 1


class GFlowCliProviderError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GFlowCliProvider:
    def __init__(
        self,
        gflow_bin: str = "gflow",
        profile: Optional[str] = None,
        state_dir: Path = Path("data/gflow_jobs"),
        generation_timeout_seconds: int = 1800,
    ) -> None:
        self.gflow_bin = gflow_bin
        self.profile = profile
        self.state_dir = state_dir.resolve()
        self.generation_timeout_seconds = generation_timeout_seconds
        self.credit_cost_by_duration = {
            4: int(os.getenv("GFLOW_CREDIT_COST_4S", "6")),
            6: int(os.getenv("GFLOW_CREDIT_COST_6S", "9")),
            8: int(os.getenv("GFLOW_CREDIT_COST_8S", "12")),
            10: int(os.getenv("GFLOW_CREDIT_COST_10S", "15")),
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def _profile_args(self) -> List[str]:
        return ["--profile", self.profile] if self.profile else []

    def _resolve_executable(self) -> Optional[str]:
        for candidate in (os.getenv("GFLOW_BIN", ""), self.gflow_bin):
            candidate = candidate.strip()
            if not candidate:
                continue
            found = shutil.which(candidate)
            if found:
                return found
            path = Path(candidate).expanduser()
            if path.is_file():
                return str(path.resolve())
        return None

    @staticmethod
    def _chrome_reachable(host: str = "127.0.0.1", port: int = 9222) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.8)
            try:
                sock.connect((host, port))
                return True
            except OSError:
                return False

    def health(self) -> FlowHealth:
        executable = self._resolve_executable()
        chrome_ok = self._chrome_reachable()
        if not executable:
            return FlowHealth(False, chrome_ok, False, "gflow-cli executable not found")
        result = subprocess.run(
            [executable, "auth", "status", *self._profile_args()],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        detail = (result.stdout or result.stderr).strip()
        return FlowHealth(
            healthy=result.returncode == 0,
            chrome_reachable=chrome_ok,
            logged_in=result.returncode == 0,
            message=detail or "gflow auth status completed",
        )

    def _state_path(self, job_id: str) -> Path:
        return self.state_dir / f"{job_id}.json"

    def _write_state(self, job_id: str, state: Dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_path(job_id)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def _read_state(self, job_id: str) -> Optional[Dict]:
        path = self._state_path(job_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _ensure_ready(self) -> str:
        health = self.health()
        if not health.healthy:
            raise GFlowCliProviderError(f"Flow health check failed: {health.message}")
        executable = self._resolve_executable()
        if not executable:
            raise GFlowCliProviderError("gflow-cli executable not found")
        return executable

    @staticmethod
    def _validate_refs(reference_paths: List[str], max_refs: int) -> List[str]:
        refs: List[str] = []
        for candidate in reference_paths:
            resolved = str(Path(candidate).resolve())
            if resolved not in refs:
                refs.append(resolved)
        if len(refs) > max_refs:
            raise GFlowCliProviderError("reference image limit exceeded")
        for ref in refs:
            if not Path(ref).is_file():
                raise GFlowCliProviderError(f"reference image missing: {ref}")
        return refs

    def generate_image(self, request: FlowImageGenerationRequest) -> FlowImageResult:
        if not request.reference_paths:
            raise GFlowCliProviderError("affiliate image generation requires reference images")
        executable = self._ensure_ready()
        refs = self._validate_refs(
            request.reference_paths,
            self.capabilities().max_reference_images,
        )

        output_path = Path(
            request.output_path or f"data/images/{request.job_id}.png"
        ).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        existing = self._read_state(request.job_id)
        if existing:
            if existing.get("idempotency_key") != request.idempotency_key:
                raise GFlowCliProviderError("job id collision")
            prior_output = existing.get("output_path")
            if existing.get("status") == "COMPLETED" and prior_output and Path(prior_output).is_file():
                return FlowImageResult(request.job_id, "COMPLETED", prior_output)
            raise GFlowCliProviderError(
                "refusing ambiguous duplicate image submission; create a new attempt"
            )

        command = [executable, "image", "i2i", request.prompt.strip()]
        for ref in refs:
            command.extend(["--ref", ref])
        command.extend(
            [
                "--model", request.model,
                "--aspect", request.aspect_ratio,
                "--count", "2",
                "--out", str(output_path.parent),
                *self._profile_args(),
            ]
        )
        state = {
            "job_id": request.job_id,
            "kind": "IMAGE_I2I",
            "idempotency_key": request.idempotency_key,
            "status": "RUNNING",
            "output_path": str(output_path),
            "command": command,
            "started_at": _utc_now(),
        }
        self._write_state(request.job_id, state)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.generation_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            state.update(status="FAILED", error_message="gflow image timed out", finished_at=_utc_now())
            self._write_state(request.job_id, state)
            raise GFlowCliProviderError("gflow image timed out") from exc

        log_dir = output_path.parent / f".{request.job_id}_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (log_dir / "stderr.log").write_text(result.stderr or "", encoding="utf-8")
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "gflow image failed").strip()
            state.update(status="FAILED", error_message=message, finished_at=_utc_now())
            self._write_state(request.job_id, state)
            raise GFlowCliProviderError(message)
        if not output_path.is_file():
            candidates = [
                p for p in output_path.parent.glob("*")
                if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                and not p.name.startswith(".")
            ]
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                target_img = candidates[0]
                if target_img.suffix.lower() != output_path.suffix.lower():
                    try:
                        from PIL import Image
                        with Image.open(target_img) as im:
                            im.save(output_path)
                        target_img.unlink(missing_ok=True)
                    except Exception:
                        shutil.move(str(target_img), str(output_path))
                else:
                    shutil.move(str(target_img), str(output_path))
            else:
                state.update(status="FAILED", error_message="no image produced", finished_at=_utc_now())
                self._write_state(request.job_id, state)
                raise GFlowCliProviderError("gflow image completed but output file was not produced")

        state.update(status="COMPLETED", finished_at=_utc_now())
        self._write_state(request.job_id, state)
        return FlowImageResult(request.job_id, "COMPLETED", str(output_path))

    def estimate(self, request: FlowGenerationRequest) -> CostQuote:
        if request.duration_seconds not in FLOW_ALLOWED_DURATIONS:
            raise GFlowCliProviderError(
                f"duration must be one of {FLOW_ALLOWED_DURATIONS}"
            )
        cost = self.credit_cost_by_duration[request.duration_seconds]
        return CostQuote(cost, request.max_credit_cost, cost <= request.max_credit_cost)

    def submit(self, request: FlowGenerationRequest) -> ProviderJobRef:
        executable = self._ensure_ready()
        quote = self.estimate(request)
        if not quote.can_proceed:
            raise GFlowCliProviderError("credit ceiling exceeded")

        existing = self._read_state(request.job_id)
        if existing:
            if existing.get("idempotency_key") != request.idempotency_key:
                raise GFlowCliProviderError("job id collision")
            outputs = [Path(p) for p in existing.get("output_paths", [])]
            if existing.get("status") == "COMPLETED" and outputs and all(p.exists() for p in outputs):
                return ProviderJobRef(request.job_id, "COMPLETED", quote.quoted_credit_cost)
            raise GFlowCliProviderError(
                "refusing ambiguous duplicate submission; create a new attempt"
            )

        out_dir = Path(request.output_directory or f"data/clips/{request.job_id}").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        refs = self._validate_refs(
            [p for p in [*request.reference_paths, request.start_frame_path] if p],
            self.capabilities().max_reference_images,
        )

        command = [executable, "video", "r2v" if refs else "t2v", request.prompt.strip()]
        for ref in refs:
            command.extend(["--ref", ref])
        command.extend(
            [
                "--aspect", request.aspect_ratio,
                "--model", FLOW_DEFAULT_MODEL,
                "--duration", str(request.duration_seconds),
                "--out-dir", str(out_dir),
                *self._profile_args(),
            ]
        )

        state = {
            "job_id": request.job_id,
            "kind": "VIDEO",
            "idempotency_key": request.idempotency_key,
            "status": "RUNNING",
            "started_at": _utc_now(),
            "output_paths": [],
            "command": command,
        }
        self._write_state(request.job_id, state)

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.generation_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            state.update(status="FAILED", error_message="gflow timed out", finished_at=_utc_now())
            self._write_state(request.job_id, state)
            raise GFlowCliProviderError("gflow timed out; inspect Flow before retry") from exc

        (out_dir / "gflow_stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (out_dir / "gflow_stderr.log").write_text(result.stderr or "", encoding="utf-8")
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "gflow failed").strip()
            state.update(status="FAILED", error_message=message, finished_at=_utc_now())
            self._write_state(request.job_id, state)
            raise GFlowCliProviderError(message)

        outputs = sorted(str(p.resolve()) for p in out_dir.rglob("*.mp4"))
        if not outputs:
            state.update(status="FAILED", error_message="no MP4 produced", finished_at=_utc_now())
            self._write_state(request.job_id, state)
            raise GFlowCliProviderError("gflow completed but no MP4 was produced")

        state.update(status="COMPLETED", output_paths=outputs, finished_at=_utc_now())
        self._write_state(request.job_id, state)
        return ProviderJobRef(request.job_id, "COMPLETED", quote.quoted_credit_cost)

    def poll(self, provider_job_id: str) -> ProviderJobStatus:
        state = self._read_state(provider_job_id)
        if not state:
            return ProviderJobStatus(provider_job_id, "UNKNOWN", error_message="job not found")
        outputs = state.get("output_paths", [])
        if not outputs and state.get("output_path"):
            outputs = [state["output_path"]]
        return ProviderJobStatus(
            provider_job_id=provider_job_id,
            status=state.get("status", "UNKNOWN"),
            progress_pct=100 if state.get("status") == "COMPLETED" else 0,
            output_paths=outputs,
            error_message=state.get("error_message"),
        )

    def download(self, provider_job_id: str, output_dir: Path) -> List[Path]:
        status = self.poll(provider_job_id)
        if status.status != "COMPLETED":
            raise GFlowCliProviderError("job is not completed")
        output_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for source in map(Path, status.output_paths):
            target = output_dir / source.name
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            copied.append(target)
        return copied

    def cancel(self, provider_job_id: str) -> None:
        raise GFlowCliProviderError(
            "desktop Flow jobs are not safely cancellable after submission"
        )
