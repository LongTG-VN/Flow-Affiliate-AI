import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional


PIPELINE_STAGES = (
    "INPUT_READY",
    "PRODUCT_EXTRACTED",
    "CHARACTER_DRESSED",
    "CHARACTER_VIDEO_READY",
    "PRODUCT_VIDEO_READY",
    "VOICE_READY",
    "RENDERED",
    "COMPLETED",
)


@dataclass
class AffiliateJobState:
    job_id: str
    character_image: str
    product_image: str
    status: str = "INPUT_READY"
    isolated_product_image: Optional[str] = None
    character_wear_image: Optional[str] = None
    character_video: Optional[str] = None
    product_video: Optional[str] = None
    voice_audio: Optional[str] = None
    captions_ass: Optional[str] = None
    final_video: Optional[str] = None
    character_video_level: int = 3
    error_stage: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


class JobStore:
    def __init__(self, root: Path = Path("data/jobs")) -> None:
        self.root = root.resolve()

    def path_for(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def load(self, job_id: str) -> Optional[AffiliateJobState]:
        path = self.path_for(job_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return AffiliateJobState(**data)

    def save(self, state: AffiliateJobState) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(state.job_id)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, path)
        return path

    def mark_error(self, state: AffiliateJobState, stage: str, message: str) -> None:
        state.error_stage = stage
        state.error_message = message
        self.save(state)

    def clear_error(self, state: AffiliateJobState) -> None:
        state.error_stage = None
        state.error_message = None
        self.save(state)
