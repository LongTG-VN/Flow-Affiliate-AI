import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .base import AuditResult, SanitizeResult


PRIVACY_TAG_HINTS = {
    "artist",
    "author",
    "comment",
    "composer",
    "copyright",
    "creation_time",
    "date",
    "description",
    "device",
    "gps",
    "location",
    "make",
    "model",
    "software",
    "title",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(text: str, lines: int = 24) -> str:
    rows = (text or "").strip().splitlines()
    return "\n".join(rows[-lines:])


def _privacy_keys(metadata: dict[str, Any]) -> list[str]:
    hits: set[str] = set()

    def scan(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                name = str(key)
                lowered = name.lower()
                if any(hint in lowered for hint in PRIVACY_TAG_HINTS):
                    hits.add(f"{prefix}.{name}" if prefix else name)
                scan(f"{prefix}.{name}" if prefix else name, child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                scan(f"{prefix}[{index}]", child)

    scan("", metadata)
    return sorted(hits)


class LocalProvenanceAuditor:
    """Read-only local audit using ffprobe and optional c2patool.

    `c2patool <asset>` is read-only and emits the asset's C2PA manifest JSON to
    stdout. If the binary is unavailable or its result is ambiguous, C2PA status
    is reported as `unknown` rather than guessed.

    No generic local detector for invisible AI watermarks is bundled here. That
    field therefore remains explicit `unknown` unless a dedicated detector is
    integrated later.
    """

    def __init__(
        self,
        *,
        ffprobe_bin: str = "ffprobe",
        c2patool_bin: str = "c2patool",
        timeout_seconds: int = 90,
    ) -> None:
        self.ffprobe_bin = shutil.which(ffprobe_bin) or ffprobe_bin
        self.c2patool_bin = shutil.which(c2patool_bin)
        self.requested_c2patool_bin = c2patool_bin
        self.timeout_seconds = timeout_seconds

    def _container_metadata(self, asset: Path) -> dict[str, Any]:
        result = subprocess.run(
            [
                self.ffprobe_bin,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_entries",
                "format=format_name,duration,size,bit_rate:format_tags:stream=index,codec_type,codec_name:stream_tags",
                str(asset),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            return {
                "available": False,
                "error": _tail(result.stderr or result.stdout or "ffprobe failed"),
            }
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            return {"available": False, "error": f"invalid ffprobe JSON: {exc}"}
        payload["available"] = True
        payload["privacy_candidate_keys"] = _privacy_keys(payload)
        return payload

    @staticmethod
    def _c2pa_summary(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {
                "available": True,
                "status": "unknown",
                "error": "c2patool returned a non-object JSON payload",
            }
        manifests = payload.get("manifests")
        active_manifest = payload.get("active_manifest")
        count = len(manifests) if isinstance(manifests, dict) else 0
        present = bool(active_manifest) or count > 0
        validation = payload.get("validation_status")
        return {
            "available": True,
            "status": "present" if present else "absent",
            "active_manifest": active_manifest,
            "manifest_count": count,
            "validation_status": validation if isinstance(validation, list) else [],
        }

    def _c2pa(self, asset: Path) -> dict[str, Any]:
        if not self.c2patool_bin:
            return {
                "available": False,
                "status": "unknown",
                "binary": self.requested_c2patool_bin,
                "reason": "c2patool is not installed or not on PATH",
            }
        try:
            result = subprocess.run(
                [self.c2patool_bin, str(asset)],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "available": True,
                "status": "unknown",
                "binary": self.c2patool_bin,
                "error": str(exc),
            }
        if result.returncode != 0:
            return {
                "available": True,
                "status": "unknown",
                "binary": self.c2patool_bin,
                "exit_code": result.returncode,
                "error": _tail(result.stderr or result.stdout or "c2patool failed"),
            }
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            return {
                "available": True,
                "status": "unknown",
                "binary": self.c2patool_bin,
                "error": f"invalid c2patool JSON: {exc}",
            }
        summary = self._c2pa_summary(payload)
        summary["binary"] = self.c2patool_bin
        return summary

    def audit(self, asset_path: str) -> AuditResult:
        asset = Path(asset_path).expanduser().resolve()
        if not asset.is_file():
            raise FileNotFoundError(f"audit asset missing: {asset}")
        return AuditResult(
            asset_path=str(asset),
            sha256=_sha256(asset),
            container_metadata=self._container_metadata(asset),
            c2pa=self._c2pa(asset),
            invisible_watermark={
                "status": "unknown",
                "detector_available": False,
                "reason": (
                    "No generic invisible-watermark detector is configured. "
                    "This audit does not attempt to remove or defeat provenance watermarks."
                ),
            },
        )


class PrivacyMetadataSanitizer:
    """Remove ordinary container metadata only when C2PA is confidently absent.

    If C2PA is present or cannot be determined, the publish file is a byte-for-byte
    copy of the rendered master. This intentionally avoids damaging provenance.
    """

    def __init__(self, *, ffmpeg_bin: str = "ffmpeg", timeout_seconds: int = 300) -> None:
        self.ffmpeg_bin = shutil.which(ffmpeg_bin) or ffmpeg_bin
        self.timeout_seconds = timeout_seconds

    def sanitize(
        self,
        *,
        source_path: str,
        output_path: str,
        c2pa_status: str,
    ) -> SanitizeResult:
        source = Path(source_path).expanduser().resolve()
        output = Path(output_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"sanitize source missing: {source}")
        output.parent.mkdir(parents=True, exist_ok=True)
        source_hash = _sha256(source)

        if c2pa_status != "absent":
            if source != output:
                shutil.copyfile(source, output)
            return SanitizeResult(
                output_path=str(output),
                performed=False,
                reason=(
                    "privacy metadata sanitization skipped because C2PA status is "
                    f"{c2pa_status}; source bytes were preserved"
                ),
                source_sha256=source_hash,
                output_sha256=_sha256(output),
            )

        temp = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp{output.suffix or '.mp4'}")
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(source),
            "-map",
            "0",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(temp),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg metadata sanitize failed ({result.returncode}): "
                    + _tail(result.stderr or result.stdout)
                )
            os.replace(temp, output)
            return SanitizeResult(
                output_path=str(output),
                performed=True,
                reason="ordinary container metadata removed with stream copy",
                source_sha256=source_hash,
                output_sha256=_sha256(output),
            )
        except Exception as exc:
            if temp.exists():
                temp.unlink()
            if source != output:
                shutil.copyfile(source, output)
            return SanitizeResult(
                output_path=str(output),
                performed=False,
                reason="metadata sanitization failed; original bytes were preserved",
                source_sha256=source_hash,
                output_sha256=_sha256(output),
                error=str(exc),
            )
