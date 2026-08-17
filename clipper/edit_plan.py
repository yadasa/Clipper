from __future__ import annotations

import json
import shutil
from pathlib import Path

from .brand import BrandKit, normalize_brand
from .config import normalize_ratios
from .models import ClipCandidate

PLAN_VERSION = 2


def _clean_intervals(raw: object, start: float, end: float) -> list[dict[str, float]]:
    if not isinstance(raw, list):
        return []
    clean: list[dict[str, float]] = []
    previous_end = -1.0
    for value in raw:
        if not isinstance(value, dict):
            continue
        try:
            item_start = float(value.get("start", 0))
            item_end = float(value.get("end", 0))
        except (TypeError, ValueError):
            continue
        if item_start < 0 or item_end <= item_start:
            raise ValueError(f"Invalid stitched source interval: {item_start}..{item_end}")
        if item_start < previous_end - 0.001:
            raise ValueError("Stitched source intervals must be chronological and non-overlapping")
        clean.append({"start": round(item_start, 4), "end": round(item_end, 4)})
        previous_end = item_end
    if len(clean) <= 1:
        return []
    if clean[0]["start"] < start - 0.01 or clean[-1]["end"] > end + 0.01:
        raise ValueError("Stitched source intervals must stay inside the clip envelope")
    return clean


def generate_edit_plan(
    project_id: str,
    candidates: list[ClipCandidate],
    ratios: list[str],
    brand: BrandKit,
    *,
    alternate_visual_layouts: bool = False,
    music_path: str | None = None,
) -> dict:
    layout_modes = ["auto", "split", "pip", "interrupt"] if alternate_visual_layouts else ["auto"]
    return {
        "version": PLAN_VERSION,
        "project_id": project_id,
        "brand": brand.to_dict(),
        "music_path": music_path,
        "defaults": {
            "caption_preset": brand.caption_preset,
            "smart_cut": True,
            "remove_fillers": True,
            "punch_ins": True,
            "hook_overlay": True,
            "broll_max_cues": None,
        },
        "clips": [
            {
                "id": candidate.id,
                "enabled": True,
                "start": round(candidate.start, 3),
                "end": round(candidate.end, 3),
                "source_intervals": list(candidate.source_intervals),
                "title": candidate.title,
                "score": candidate.score,
                "metrics": dict(candidate.metrics),
                "transcript": candidate.transcript,
                "ratios": list(ratios),
                "layout_modes": list(layout_modes),
                "caption_preset": brand.caption_preset,
                "smart_cut": True,
                "remove_fillers": True,
                "punch_ins": True,
                "hook_overlay": True,
                "hook_text": None,
                "broll_max_cues": None,
                "auto_profile": {},
            }
            for candidate in candidates
        ],
    }


def validate_edit_plan(plan: dict) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("Edit plan must be a JSON object")
    version = int(plan.get("version", 0))
    if version not in {1, PLAN_VERSION}:
        raise ValueError(f"Unsupported edit plan version: {plan.get('version')}")
    # V1 is upgraded in memory; saves always write V2.
    plan["version"] = PLAN_VERSION
    plan["brand"] = normalize_brand(plan.get("brand")).to_dict()
    defaults = dict(plan.get("defaults") or {})
    clips = plan.get("clips")
    if not isinstance(clips, list):
        raise ValueError("Edit plan clips must be a list")
    seen = set()
    clean = []
    for raw in clips:
        if not isinstance(raw, dict):
            continue
        clip_id = str(raw.get("id") or "").strip()
        if not clip_id or clip_id in seen:
            raise ValueError(f"Duplicate or missing clip id: {clip_id!r}")
        seen.add(clip_id)
        start = float(raw.get("start", 0))
        end = float(raw.get("end", 0))
        if start < 0 or end <= start:
            raise ValueError(f"Invalid clip range for {clip_id}: {start}..{end}")
        intervals = _clean_intervals(raw.get("source_intervals"), start, end)
        ratios = normalize_ratios(raw.get("ratios") or ["9:16"])
        modes = [m for m in raw.get("layout_modes", ["auto"]) if m in {"auto", "split", "pip", "interrupt"}]
        if not modes:
            modes = ["auto"]
        preset = str(raw.get("caption_preset") or defaults.get("caption_preset") or "karaoke")
        if preset not in {"karaoke", "clean", "minimal"}:
            preset = "karaoke"
        cue_value = raw.get("broll_max_cues", defaults.get("broll_max_cues"))
        try:
            broll_max_cues = max(0, min(12, int(cue_value))) if cue_value is not None else None
        except (TypeError, ValueError):
            broll_max_cues = None
        clean.append({
            **raw,
            "id": clip_id,
            "enabled": bool(raw.get("enabled", True)),
            "start": start,
            "end": end,
            "source_intervals": intervals,
            "title": str(raw.get("title") or clip_id)[:160],
            "score": float(raw.get("score", 0)),
            "metrics": dict(raw.get("metrics") or {}),
            "transcript": str(raw.get("transcript") or ""),
            "ratios": ratios,
            "layout_modes": modes,
            "caption_preset": preset,
            "smart_cut": bool(raw.get("smart_cut", defaults.get("smart_cut", True))),
            "remove_fillers": bool(raw.get("remove_fillers", defaults.get("remove_fillers", True))),
            "punch_ins": bool(raw.get("punch_ins", defaults.get("punch_ins", True))),
            "hook_overlay": bool(raw.get("hook_overlay", defaults.get("hook_overlay", True))),
            "hook_text": str(raw.get("hook_text"))[:120] if raw.get("hook_text") else None,
            "broll_max_cues": broll_max_cues,
            "auto_profile": dict(raw.get("auto_profile") or {}),
        })
    plan["clips"] = clean
    plan["defaults"] = defaults
    music = plan.get("music_path")
    plan["music_path"] = str(music) if music else None
    return plan


def _persist_asset(value: str | None, project_root: Path, stem: str) -> str | None:
    """Keep a plan asset self-contained and return a project-relative path."""
    if not value:
        return None
    root = project_root.resolve()
    source = Path(value).expanduser()
    if not source.is_absolute():
        resolved = (root / source).resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            source = resolved
        else:
            if resolved.is_file():
                return relative.as_posix()
            source = resolved
    else:
        source = source.resolve()
    if not source.is_file():
        return None

    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() or ".bin"
    destination = assets / f"{stem}{suffix}"
    if source != destination.resolve():
        shutil.copy2(source, destination)
    return destination.relative_to(root).as_posix()


def save_edit_plan(plan: dict, path: str | Path) -> Path:
    clean = validate_edit_plan(plan)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    brand = dict(clean.get("brand") or {})
    brand["logo_path"] = _persist_asset(brand.get("logo_path"), out.parent, "logo")
    clean["brand"] = brand
    clean["music_path"] = _persist_asset(clean.get("music_path"), out.parent, "music")

    temp = out.with_suffix(".tmp")
    temp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(out)
    return out


def load_edit_plan(path: str | Path) -> dict:
    return validate_edit_plan(json.loads(Path(path).read_text(encoding="utf-8")))


def candidate_from_plan(item: dict) -> ClipCandidate:
    return ClipCandidate(
        id=str(item["id"]),
        start=float(item["start"]),
        end=float(item["end"]),
        score=float(item.get("score", 0)),
        title=str(item.get("title") or item["id"]),
        reason="Edit plan",
        transcript=str(item.get("transcript") or ""),
        metrics={str(k): float(v) for k, v in dict(item.get("metrics") or {}).items()},
        source_intervals=[dict(value) for value in item.get("source_intervals") or []],
    )