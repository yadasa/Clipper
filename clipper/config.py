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


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass(slots=True)
class Settings:
    workdir: Path = field(default_factory=lambda: Path(_str_env("CLIPPER_WORKDIR", "./data")).resolve())
    whisper_model: str = _str_env("WHISPER_MODEL", "small")
    whisper_device: str = _str_env("WHISPER_DEVICE", "auto")
    whisper_batch_size: int = _int_env("WHISPER_BATCH_SIZE", 8)
    max_clips: int = _int_env("MAX_CLIPS", 8)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = _str_env("GEMINI_MODEL", "gemini-2.5-flash")
    visual_provider: str = _str_env("VISUAL_PROVIDER", "commons").lower()
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
