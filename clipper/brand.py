from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_PRESETS = {"karaoke", "clean", "minimal"}


@dataclass(slots=True)
class BrandKit:
    name: str = "default"
    font: str = "Arial"
    primary_text: str = "#FFFFFF"
    accent: str = "#D6A77A"
    outline: str = "#201A16"
    caption_preset: str = "karaoke"
    logo_path: str | None = None
    logo_position: str = "top-right"
    hook_box: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _valid_color(value: str, fallback: str) -> str:
    return value if _HEX.match(str(value or "")) else fallback


def normalize_brand(data: dict | None) -> BrandKit:
    data = dict(data or {})
    kit = BrandKit(
        name=str(data.get("name") or "default")[:80],
        font=str(data.get("font") or "Arial")[:120],
        primary_text=_valid_color(str(data.get("primary_text") or "#FFFFFF"), "#FFFFFF"),
        accent=_valid_color(str(data.get("accent") or "#D6A77A"), "#D6A77A"),
        outline=_valid_color(str(data.get("outline") or "#201A16"), "#201A16"),
        caption_preset=str(data.get("caption_preset") or "karaoke"),
        logo_path=str(data.get("logo_path")) if data.get("logo_path") else None,
        logo_position=str(data.get("logo_position") or "top-right"),
        hook_box=bool(data.get("hook_box", True)),
    )
    if kit.caption_preset not in _PRESETS:
        kit.caption_preset = "karaoke"
    if kit.logo_position not in {"top-left", "top-right", "bottom-left", "bottom-right"}:
        kit.logo_position = "top-right"
    if kit.logo_path and not Path(kit.logo_path).expanduser().is_file():
        kit.logo_path = None
    return kit


def load_brand(path: str | Path | None) -> BrandKit:
    if not path:
        preset = os.getenv("CAPTION_PRESET", "karaoke").strip().lower()
        return BrandKit(caption_preset=preset if preset in _PRESETS else "karaoke")
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"Brand kit not found: {p}")
    return normalize_brand(json.loads(p.read_text(encoding="utf-8")))


def save_brand(kit: BrandKit, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kit.to_dict(), indent=2), encoding="utf-8")
    return out
