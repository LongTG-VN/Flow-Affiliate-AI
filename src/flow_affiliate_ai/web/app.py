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

from flow_affiliate_ai.cli import build_pipeline
from flow_affiliate_ai.jobs import JobStore
from flow_affiliate_ai.pipeline import OVERLAY_POSITIONS, PRODUCT_VIDEO_SOURCES
from flow_affiliate_ai.prompts.fashion import (
    EXTRACT_PRODUCT_PROMPT,
    MAX_PROMPT_CHARS,
    PRODUCT_VIDEO_PROMPTS,
    WEAR_PRODUCT_PROMPT,
    character_video_attempt,
    default_prompt_payload,
)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
ASSET_FIELDS = {
    "isolated_product": "isolated_product_image",
    "character_wear": "character_wear_image",
    "character_video": "character_video",
    "product_video": "product_video",
    "voice_audio": "voice_audio",
    "final_video": "final_video",
}


@dataclass(frozen=True)
class WebJobConfig:
    job_id: str
    character_image: str
    product_image: str
    extract_product_prompt: str = EXTRACT_PRODUCT_PROMPT
    wear_product_prompt: str = WEAR_PRODUCT_PROMPT
    character_video_prompt: str = character_video_attempt(3).prompt
    product_video_prompt: str = PRODUCT_VIDEO_PROMPTS["zoom"]
    tts_provider: str = "gemini"
    voice: str = "Zephyr"
    product_video_style: str = "zoom"
    product_video_source: str = "worn"
    music_track: Optional[str] = None
    overlay_image: Optional[str] = None
    overlay_position: str = "bottom-right"
    overlay_width_pct: float = 26.0
    max_credit_per_video: int = 15

    @property
    def prompts(self) -> dict[str, str]:
        return {
            "extract_product": self.extract_product_prompt,
            "wear_product": self.wear_product_prompt,
            "character_video": self.character_video_prompt,
            "product_video": self.product_video_prompt,
        }


class ConfigStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path_for(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def save(self, config: WebJobConfig) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(config.job_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def load(self, job_id: str) -> Optional[WebJobConfig]:
        path = self.path_for(job_id)
        if not path.is_file():
            return None
        return WebJobConfig(**json.loads(path.read_text(encoding="utf-8")))


class JobRunner:
    def __init__(self, data_root: Path, pipeline_builder: Callable[..., object]) -> None:
        self.data_root = data_root.resolve()
        self.pipeline_builder = pipeline_builder
        self.configs = ConfigStore(self.data_root / "web_jobs")
        self.jobs = JobStore(self.data_root / "jobs")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flow-affiliate")
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

    def submit(self, config: WebJobConfig, *, approve_retry: bool = False) -> None:
        with self._lock:
            if self._futures.get(config.job_id) and not self._futures[config.job_id].done():
                raise RuntimeError("job is already running")
            self._errors.pop(config.job_id, None)
            self.configs.save(config)
            self._futures[config.job_id] = self.executor.submit(self._run, config, approve_retry)

    def _run(self, config: WebJobConfig, approve_retry: bool) -> None:
        try:
            pipeline = self.pipeline_builder(tts_provider=config.tts_provider, data_root=self.data_root)
            pipeline.run(
                job_id=config.job_id,
                character_image=config.character_image,
                product_image=config.product_image,
                extract_product_prompt=config.extract_product_prompt,
                wear_product_prompt=config.wear_product_prompt,
                character_video_prompt=config.character_video_prompt,
                product_video_prompt=config.product_video_prompt,
                voice=config.voice,
                product_video_style=config.product_video_style,
                product_video_source=config.product_video_source,
                music_track=config.music_track,
                overlay_image=config.overlay_image,
                overlay_position=config.overlay_position,
                overlay_width_pct=config.overlay_width_pct,
                approve_video_credits=True,
                approve_paid_retry=approve_retry,
                max_credit_per_video=config.max_credit_per_video,
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


def _prompt_value(value: Optional[str], default: str, label: str) -> str:
    prompt = (value if value is not None else default).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail=f"{label} cannot be empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"{label} exceeds {MAX_PROMPT_CHARS} characters",
        )
    return prompt


async def _save_upload(
    upload: UploadFile,
    directory: Path,
    stem: str,
    allowed_suffixes: set[str] = ALLOWED_IMAGE_SUFFIXES,
) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed_suffixes:
        accepted = ", ".join(sorted(allowed_suffixes))
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
        raise HTTPException(status_code=403, detail="asset is outside the local data directory") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="asset file missing")
    return path


def create_app(*, data_root: Path = Path("data"), pipeline_builder: Callable[..., object] = build_pipeline) -> FastAPI:
    data_root = data_root.resolve()
    static_dir = Path(__file__).parent / "static"
    runner = JobRunner(data_root, pipeline_builder)
    app = FastAPI(title="Flow Affiliate AI", version="0.5.0")
    app.state.runner = runner
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/prompts/defaults")
    def prompt_defaults(product_video_style: str = "zoom") -> dict[str, str]:
        if product_video_style not in PRODUCT_VIDEO_PROMPTS:
            raise HTTPException(status_code=400, detail="invalid product video style")
        return default_prompt_payload(product_video_style)

    @app.get("/api/health")
    def health(tts_provider: str = "gemini") -> dict:
        if tts_provider not in {"gemini", "edge"}:
            raise HTTPException(status_code=400, detail="invalid tts provider")
        pipeline = pipeline_builder(tts_provider=tts_provider, data_root=data_root)
        return {
            "flow": asdict(pipeline.flow.provider.health()),
            "tts": asdict(pipeline.voice.provider.health()),
            "ffmpeg": {"healthy": bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))},
        }

    @app.post("/api/jobs", status_code=202)
    async def create_job(
        character: UploadFile = File(...),
        product: UploadFile = File(...),
        sticker: Optional[UploadFile] = File(None),
        music: Optional[UploadFile] = File(None),
        job_id: Optional[str] = Form(None),
        extract_product_prompt: Optional[str] = Form(None),
        wear_product_prompt: Optional[str] = Form(None),
        character_video_prompt: Optional[str] = Form(None),
        product_video_prompt: Optional[str] = Form(None),
        tts_provider: str = Form("gemini"),
        voice: str = Form("Zephyr"),
        product_video_style: str = Form("zoom"),
        product_video_source: str = Form("worn"),
        overlay_position: str = Form("bottom-right"),
        overlay_width_pct: float = Form(26.0),
        max_credit_per_video: int = Form(15),
        approve_video_credits: bool = Form(False),
    ) -> dict:
        if not approve_video_credits:
            raise HTTPException(status_code=400, detail="Flow video credit approval is required")
        if tts_provider not in {"gemini", "edge"} or product_video_style not in {"zoom", "pan"}:
            raise HTTPException(status_code=400, detail="invalid generation option")
        if product_video_source not in PRODUCT_VIDEO_SOURCES:
            raise HTTPException(status_code=400, detail="invalid product video source")
        if overlay_position not in OVERLAY_POSITIONS:
            raise HTTPException(status_code=400, detail="invalid overlay position")
        if not 1 <= overlay_width_pct <= 50:
            raise HTTPException(status_code=400, detail="overlay width must be between 1 and 50 percent")
        if not 0 <= max_credit_per_video <= 1000:
            raise HTTPException(status_code=400, detail="invalid credit ceiling")

        defaults = default_prompt_payload(product_video_style)
        prompts = {
            "extract_product": _prompt_value(
                extract_product_prompt,
                defaults["extract_product"],
                "extract product prompt",
            ),
            "wear_product": _prompt_value(
                wear_product_prompt,
                defaults["wear_product"],
                "wear product prompt",
            ),
            "character_video": _prompt_value(
                character_video_prompt,
                defaults["character_video"],
                "character video prompt",
            ),
            "product_video": _prompt_value(
                product_video_prompt,
                defaults["product_video"],
                "product video prompt",
            ),
        }

        resolved_id = _job_id(job_id)
        if runner.configs.load(resolved_id) or runner.jobs.load(resolved_id):
            raise HTTPException(status_code=409, detail="job_id already exists")
        upload_dir = data_root / "uploads" / resolved_id
        character_path = await _save_upload(character, upload_dir, "character")
        product_path = await _save_upload(product, upload_dir, "product")
        overlay_path = None
        if sticker is not None:
            if sticker.filename:
                overlay_path = await _save_upload(sticker, upload_dir, "sticker")
            else:
                await sticker.close()
        music_path = None
        if music is not None:
            if music.filename:
                music_path = await _save_upload(
                    music, upload_dir, "music", allowed_suffixes=ALLOWED_AUDIO_SUFFIXES
                )
            else:
                await music.close()

        config = WebJobConfig(
            job_id=resolved_id,
            character_image=character_path,
            product_image=product_path,
            extract_product_prompt=prompts["extract_product"],
            wear_product_prompt=prompts["wear_product"],
            character_video_prompt=prompts["character_video"],
            product_video_prompt=prompts["product_video"],
            tts_provider=tts_provider,
            voice=voice.strip() or "Zephyr",
            product_video_style=product_video_style,
            product_video_source=product_video_source,
            music_track=music_path,
            overlay_image=overlay_path,
            overlay_position=overlay_position,
            overlay_width_pct=overlay_width_pct,
            max_credit_per_video=max_credit_per_video,
        )
        runner.submit(config)
        return {"job_id": resolved_id, "status_url": f"/api/jobs/{resolved_id}"}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        state = runner.jobs.load(job_id)
        config = runner.configs.load(job_id)
        if state is None and config is None:
            raise HTTPException(status_code=404, detail="job not found")
        payload = asdict(state) if state else {"job_id": job_id, "status": "QUEUED"}
        payload["running"] = runner.running(job_id)
        payload["web_error"] = runner.error(job_id)
        payload["prompts"] = config.prompts if config else payload.get("prompts", {})
        if config:
            payload["render_options"] = {
                "product_video_source": config.product_video_source,
                "music_enabled": bool(config.music_track),
                "overlay_enabled": bool(config.overlay_image),
                "overlay_position": config.overlay_position,
                "overlay_width_pct": config.overlay_width_pct,
            }
        payload["assets"] = {}
        if state:
            for kind, field_name in ASSET_FIELDS.items():
                value = getattr(state, field_name, None)
                if value:
                    try:
                        _safe_asset(value, data_root)
                        payload["assets"][kind] = f"/api/jobs/{job_id}/assets/{kind}"
                    except HTTPException:
                        pass
        return payload

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str) -> dict:
        config = runner.configs.load(job_id)
        if config is None:
            raise HTTPException(status_code=404, detail="job not found")
        if runner.running(job_id):
            raise HTTPException(status_code=409, detail="job is already running")
        runner.submit(config, approve_retry=True)
        return {"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}

    @app.get("/api/jobs/{job_id}/assets/{kind}")
    def asset(job_id: str, kind: str, download: bool = False) -> FileResponse:
        field_name = ASSET_FIELDS.get(kind)
        state = runner.jobs.load(job_id)
        if field_name is None or state is None:
            raise HTTPException(status_code=404, detail="asset not found")
        value = getattr(state, field_name, None)
        if not value:
            raise HTTPException(status_code=404, detail="asset not ready")
        path = _safe_asset(value, data_root)
        return FileResponse(
            path,
            filename=path.name if download else None,
            content_disposition_type="attachment" if download else "inline",
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "flow_affiliate_ai.web.app:app",
        host="127.0.0.1",
        port=int(os.getenv("FLOW_AFFILIATE_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
