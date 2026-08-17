from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ASPECT_PRESETS = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


def _str_env(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw.strip()))
    except ValueError:
        return default


def _float_env(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    values: list[str] = []
    for item in raw.split(","):
        value = item.strip().lower()
        if value and value not in values:
            values.append(value)
    return tuple(values)


@dataclass(slots=True)
class Settings:
    workdir: Path = field(default_factory=lambda: Path(_str_env("CLIPPER_WORKDIR", "./data")).resolve())
    whisper_model: str = _str_env("WHISPER_MODEL", "small")
    whisper_device: str = _str_env("WHISPER_DEVICE", "auto")
    whisper_batch_size: int = _int_env("WHISPER_BATCH_SIZE", 8)
    max_clips: int = _int_env("MAX_CLIPS", 8)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = _str_env("GEMINI_MODEL", "gemini-2.5-flash")

    # First-class editing mode. Auto is intentionally the default creator flow;
    # manual keeps the older direct transcribe -> select -> per-clip cleanup path.
    automation_mode: str = _str_env("AUTOMATION_MODE", "auto").lower()
    auto_global_cleanup: bool = _bool_env("AUTO_GLOBAL_CLEANUP", True)
    auto_story_stitch: bool = _bool_env("AUTO_STORY_STITCH", True)
    auto_visual_intensity: bool = _bool_env("AUTO_VISUAL_INTENSITY", True)
    auto_quality_gate: bool = _bool_env("AUTO_QUALITY_GATE", True)
    auto_sync_refine_confidence: float = _float_env(
        "AUTO_SYNC_REFINE_CONFIDENCE", 0.24, minimum=0.0, maximum=1.0
    )
    auto_cleanup_max_removed_ratio: float = _float_env(
        "AUTO_CLEANUP_MAX_REMOVED_RATIO", 0.58, minimum=0.05, maximum=0.85
    )
    auto_min_clip_seconds: int = _int_env("AUTO_MIN_CLIP_SECONDS", 15, minimum=6)
    auto_max_clip_seconds: int = _int_env("AUTO_MAX_CLIP_SECONDS", 55, minimum=12)

    # B-roll. VISUAL_PROVIDER remains as a compatibility/coarse switch while
    # BROLL_PROVIDERS controls the ordered resolver waterfall.
    visual_provider: str = _str_env("VISUAL_PROVIDER", "auto").lower()
    broll_providers: tuple[str, ...] = field(default_factory=lambda: _csv_env("BROLL_PROVIDERS"))
    broll_library_path: str = os.getenv("BROLL_LIBRARY", "").strip()
    broll_auto_insert: bool = _bool_env("BROLL_AUTO_INSERT", True)
    broll_max_cues: int = _int_env("BROLL_MAX_CUES", 6, minimum=1)
    broll_min_relevance: float = _float_env(
        "BROLL_MIN_RELEVANCE", 0.30, minimum=0.0, maximum=1.0
    )
    broll_max_download_mb: int = _int_env("BROLL_MAX_DOWNLOAD_MB", 80, minimum=5)
    broll_search_cache_hours: int = _int_env("BROLL_SEARCH_CACHE_HOURS", 24, minimum=1)
    pexels_api_key: str = os.getenv("PEXELS_API_KEY", "").strip()
    pixabay_api_key: str = os.getenv("PIXABAY_API_KEY", "").strip()
    diffusion_model: str = os.getenv("DIFFUSION_MODEL", "").strip()

    brand_kit_path: str = os.getenv("BRAND_KIT", "").strip()
    music_path: str = os.getenv("BACKGROUND_MUSIC", "").strip()
    caption_preset: str = _str_env("CAPTION_PRESET", "karaoke")
    smart_cut: bool = _bool_env("SMART_CUT", True)
    remove_fillers: bool = _bool_env("REMOVE_FILLERS", True)
    punch_ins: bool = _bool_env("PUNCH_INS", True)
    hook_overlay: bool = _bool_env("HOOK_OVERLAY", True)
    stage_cache: bool = _bool_env("STAGE_CACHE", True)
    auto_hardware_profile: bool = _bool_env("AUTO_HARDWARE_PROFILE", True)
    firebase_project_id: str = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    firebase_storage_bucket: str = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
    firebase_poll_seconds: int = _int_env("FIREBASE_POLL_SECONDS", 8, minimum=2)
    firebase_worker_id: str = os.getenv("FIREBASE_WORKER_ID", "").strip()
    upload_post_api_key: str = os.getenv("UPLOAD_POST_API_KEY", "").strip()
    upload_post_user: str = os.getenv("UPLOAD_POST_USER", "").strip()

    def ensure_dirs(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "projects").mkdir(exist_ok=True)
        (self.workdir / "cache").mkdir(exist_ok=True)

    @property
    def auto_mode(self) -> bool:
        return self.automation_mode.strip().lower() == "auto"

    def apply_hardware_profile(self):
        if not self.auto_hardware_profile:
            return None
        from .hardware import apply_profile_defaults
        return apply_profile_defaults(self)


def normalize_ratios(ratios: list[str] | tuple[str, ...] | None) -> list[str]:
    values = list(ratios or ["9:16"])
    clean: list[str] = []
    for ratio in values:
        ratio = str(ratio).strip()
        if ratio not in ASPECT_PRESETS:
            raise ValueError(f"Unsupported aspect ratio {ratio!r}; choose from {', '.join(ASPECT_PRESETS)}")
        if ratio not in clean:
            clean.append(ratio)
    return clean