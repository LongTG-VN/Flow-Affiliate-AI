from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from flow_affiliate_ai.factory_cli import build_factory_pipeline
from flow_affiliate_ai.jobs import JobStore


MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")


@dataclass(frozen=True)
class FactoryJobConfig:
    job_id: str
    model_image: str
    product_image: str
    logo_image: str
    max_credit_per_video: int = 15
    overlay_position: str = "bottom-right"
    overlay_width_pct: float = 14.0
    overlay_margin_px: int = 40


class FactoryConfigStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path_for(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def save(self, config: FactoryJobConfig) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(config.job_id)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, path)

    def load(self, job_id: str) -> Optional[FactoryJobConfig]:
        path = self.path_for(job_id)
        if not path.is_file():
            return None
        return FactoryJobConfig(**json.loads(path.read_text(encoding="utf-8")))


class FactoryJobRunner:
    def __init__(self, data_root: Path, pipeline_builder: Callable[..., object]) -> None:
        self.data_root = data_root.resolve()
        self.pipeline_builder = pipeline_builder
        self.configs = FactoryConfigStore(self.data_root / "factory_jobs")
        self.jobs = JobStore(self.data_root / "jobs")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flow-factory")
        self._futures: dict[str, Future] = {}
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def running(self, job_id: str) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
            return bool(future and not future.done())

    def error(self, job_id: str) -> Optional[str]:
        with self._lock:
            return self._errors.get(job_id)

    def submit(
        self,
        config: FactoryJobConfig,
        *,
        approve_video_credits: bool,
        approve_paid_retry: bool = False,
    ) -> None:
        with self._lock:
            future = self._futures.get(config.job_id)
            if future and not future.done():
                raise RuntimeError("job is already running")
            self._errors.pop(config.job_id, None)
            self.configs.save(config)
            self._futures[config.job_id] = self.executor.submit(
                self._run,
                config,
                approve_video_credits,
                approve_paid_retry,
            )

    def _run(
        self,
        config: FactoryJobConfig,
        approve_video_credits: bool,
        approve_paid_retry: bool,
    ) -> None:
        try:
            pipeline = self.pipeline_builder(data_root=self.data_root)
            pipeline.run(
                job_id=config.job_id,
                model_image=config.model_image,
                product_image=config.product_image,
                logo_image=config.logo_image,
                approve_video_credits=approve_video_credits,
                approve_paid_retry=approve_paid_retry,
                max_credit_per_video=config.max_credit_per_video,
                overlay_position=config.overlay_position,
                overlay_width_pct=config.overlay_width_pct,
                overlay_margin_px=config.overlay_margin_px,
            )
        except Exception as exc:
            with self._lock:
                self._errors[config.job_id] = str(exc)


def _job_id(value: Optional[str]) -> str:
    if not value:
        return f"job-{uuid.uuid4().hex[:10]}"
    value = value.strip()
    if not JOB_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid job_id")
    return value


async def _save_image(upload: UploadFile, directory: Path, stem: str) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        accepted = ", ".join(sorted(ALLOWED_IMAGE_SUFFIXES))
        raise HTTPException(status_code=400, detail=f"only {accepted} files are accepted")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{stem}{suffix}"
    temp = directory / f".{stem}{suffix}.tmp"
    size = 0
    try:
        with temp.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise HTTPException(status_code=413, detail="upload exceeds 20 MB")
                handle.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty upload")
        os.replace(temp, target)
        return str(target.resolve())
    finally:
        await upload.close()
        if temp.exists():
            temp.unlink()


def _safe_asset(path_text: str, data_root: Path) -> Path:
    path = Path(path_text).resolve()
    try:
        path.relative_to(data_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="asset is outside local data directory") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="asset file missing")
    return path


def _asset_path(state, asset_name: str) -> Optional[str]:
    if asset_name == "isolated_product":
        return state.isolated_product_image
    if asset_name == "worn_model":
        return state.canonical_worn_image or state.character_wear_image
    if asset_name == "final_video":
        return state.final_video
    if asset_name == "logo":
        return state.logo_image
    if re.fullmatch(r"shot_\d{2}", asset_name):
        result = state.shot_results.get(asset_name, {})
        return result.get("output_path")
    return None


def create_app(
    *,
    data_root: Path = Path("data"),
    pipeline_builder: Callable[..., object] = build_factory_pipeline,
) -> FastAPI:
    data_root = data_root.resolve()
    static_dir = Path(__file__).parent / "static"
    runner = FactoryJobRunner(data_root, pipeline_builder)
    app = FastAPI(title="Flow Affiliate Factory", version="0.7.0")
    app.state.runner = runner
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health() -> dict:
        ffmpeg_ok = bool(shutil.which(os.getenv("FFMPEG_BIN", "ffmpeg")))
        ffprobe_ok = bool(shutil.which(os.getenv("FFPROBE_BIN", "ffprobe")))
        planner_configured = bool(os.getenv("OPENAI_API_KEY"))
        flow_health = None
        try:
            pipeline = pipeline_builder(data_root=data_root)
            flow_health = asdict(pipeline.flow.provider.health())
        except Exception as exc:
            flow_health = {"healthy": False, "message": str(exc)}
        return {
            "flow": flow_health,
            "planner": {
                "configured": planner_configured,
                "model": os.getenv("OPENAI_PLANNER_MODEL", "gpt-5.6-luna"),
            },
            "ffmpeg": {"healthy": ffmpeg_ok and ffprobe_ok},
        }

    @app.post("/api/jobs", status_code=202)
    async def create_job(
        model: UploadFile = File(...),
        product: UploadFile = File(...),
        logo: UploadFile = File(...),
        job_id: Optional[str] = Form(None),
        max_credit_per_video: int = Form(15),
        overlay_position: str = Form("bottom-right"),
        overlay_width_pct: float = Form(14.0),
        overlay_margin_px: int = Form(40),
    ) -> dict:
        if not 0 <= max_credit_per_video <= 1000:
            raise HTTPException(status_code=400, detail="invalid credit ceiling")
        if overlay_position not in {
            "top-left", "top-right", "bottom-left", "bottom-right", "center"
        }:
            raise HTTPException(status_code=400, detail="invalid logo position")
        if not 1 <= overlay_width_pct <= 50:
            raise HTTPException(status_code=400, detail="logo width must be between 1 and 50 percent")
        if overlay_margin_px < 0:
            raise HTTPException(status_code=400, detail="logo margin cannot be negative")

        resolved_id = _job_id(job_id)
        if runner.configs.load(resolved_id) or runner.jobs.load(resolved_id):
            raise HTTPException(status_code=409, detail="job_id already exists")

        upload_dir = data_root / "uploads" / resolved_id
        model_path = await _save_image(model, upload_dir, "model")
        product_path = await _save_image(product, upload_dir, "product")
        logo_path = await _save_image(logo, upload_dir, "logo")
        config = FactoryJobConfig(
            job_id=resolved_id,
            model_image=model_path,
            product_image=product_path,
            logo_image=logo_path,
            max_credit_per_video=max_credit_per_video,
            overlay_position=overlay_position,
            overlay_width_pct=overlay_width_pct,
            overlay_margin_px=overlay_margin_px,
        )
        try:
            runner.submit(config, approve_video_credits=False)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job_id": resolved_id, "status": "QUEUED_FOR_PLANNING"}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        config = runner.configs.load(job_id)
        state = runner.jobs.load(job_id)
        if config is None and state is None:
            raise HTTPException(status_code=404, detail="job not found")
        payload = asdict(state) if state else {"job_id": job_id, "status": "QUEUED"}
        payload["running"] = runner.running(job_id)
        payload["runner_error"] = runner.error(job_id)
        if state is not None:
            payload["asset_urls"] = {
                "isolated_product": f"/api/jobs/{job_id}/assets/isolated_product"
                if state.isolated_product_image
                else None,
                "worn_model": f"/api/jobs/{job_id}/assets/worn_model"
                if (state.canonical_worn_image or state.character_wear_image)
                else None,
                "final_video": f"/api/jobs/{job_id}/assets/final_video"
                if state.final_video
                else None,
            }
        return payload

    @app.post("/api/jobs/{job_id}/approve", status_code=202)
    def approve_job(
        job_id: str,
        approve_paid_retry: bool = Form(False),
    ) -> dict:
        config = runner.configs.load(job_id)
        if config is None:
            raise HTTPException(status_code=404, detail="job config not found")
        state = runner.jobs.load(job_id)
        if state is None or not state.shot_plan:
            raise HTTPException(status_code=409, detail="job is not ready for approval")
        try:
            runner.submit(
                config,
                approve_video_credits=True,
                approve_paid_retry=approve_paid_retry,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job_id": job_id, "status": "QUEUED_FOR_RENDER"}

    @app.get("/api/jobs/{job_id}/assets/{asset_name}")
    def asset(job_id: str, asset_name: str) -> FileResponse:
        state = runner.jobs.load(job_id)
        if state is None:
            raise HTTPException(status_code=404, detail="job not found")
        path_text = _asset_path(state, asset_name)
        if not path_text:
            raise HTTPException(status_code=404, detail="asset unavailable")
        return FileResponse(_safe_asset(path_text, data_root))

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=int(os.getenv("FLOW_FACTORY_PORT", "8010")),
    )


if __name__ == "__main__":
    main()
