from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from .brand import BrandKit, normalize_brand
from .config import ASPECT_PRESETS, normalize_ratios
from .models import ClipCandidate

PLAN_VERSION = 3
SUPPORTED_PLAN_VERSIONS = {1, 2, PLAN_VERSION}
DEFAULT_FPS = 30
DEFAULT_TRACK_KINDS = (
    ("source", "Source video"),
    ("broll", "B-roll"),
    ("graphics", "Graphics"),
    ("captions", "Captions"),
    ("music", "Music"),
    ("sfx", "Sound effects"),
    ("transitions", "Transitions"),
)


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


def _seconds_to_frames(seconds: float, fps: int = DEFAULT_FPS) -> int:
    return max(1, int(round(max(0.0, seconds) * fps)))


def _ratio_dimensions(ratio: str) -> tuple[int, int]:
    return ASPECT_PRESETS.get(ratio, ASPECT_PRESETS["9:16"])


def _track_id(clip_id: str, kind: str) -> str:
    return f"{clip_id}:track:{kind}"


def _item_id(clip_id: str, kind: str, suffix: str = "main") -> str:
    return f"{clip_id}:item:{kind}:{suffix}"


def _make_tracks(clip_id: str) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for z, (kind, label) in enumerate(DEFAULT_TRACK_KINDS, start=1):
        tracks.append(
            {
                "id": _track_id(clip_id, kind),
                "clip_id": clip_id,
                "kind": kind,
                "name": label,
                "z_index": z * 10,
                "hidden": False,
                "muted": False,
                "locked": False,
            }
        )
    return tracks


def _default_transform() -> dict[str, Any]:
    return {
        "x": 0.0,
        "y": 0.0,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "rotation": 0.0,
        "opacity": 1.0,
        "anchor_x": 0.5,
        "anchor_y": 0.5,
        "crop": {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0},
        "border_radius": 0.0,
    }


def _default_animation() -> dict[str, list[dict[str, Any]]]:
    return {}


def _make_scene_graph_item(candidate: ClipCandidate, ratios: list[str], fps: int) -> tuple[dict, list[dict], list[dict]]:
    ratio = ratios[0] if ratios else "9:16"
    width, height = _ratio_dimensions(ratio)
    duration_frames = _seconds_to_frames(candidate.duration, fps)
    composition = {
        "id": candidate.id,
        "name": candidate.title or candidate.id,
        "fps": fps,
        "width": width,
        "height": height,
        "duration_frames": duration_frames,
        "base_ratio": ratio,
        "ratio_variants": list(ratios),
        "safe_zone_target": "auto",
        "background": "#11100e",
    }
    tracks = _make_tracks(candidate.id)
    source_item = {
        "id": _item_id(candidate.id, "video"),
        "clip_id": candidate.id,
        "track_id": _track_id(candidate.id, "source"),
        "type": "video",
        "asset_id": "source:primary",
        "name": "Primary source",
        "from_frame": 0,
        "duration_frames": duration_frames,
        "trim_before_seconds": round(candidate.start, 4),
        "source_intervals": [dict(value) for value in candidate.source_intervals],
        "transform": _default_transform(),
        "animations": _default_animation(),
        "volume": 1.0,
        "fade_in_frames": 0,
        "fade_out_frames": 0,
        "enabled": True,
        "locked": False,
        "automation": {"source": "clipper-auto", "editable": True},
    }
    captions_item = {
        "id": _item_id(candidate.id, "captions"),
        "clip_id": candidate.id,
        "track_id": _track_id(candidate.id, "captions"),
        "type": "captions",
        "caption_document_id": f"captions:{candidate.id}",
        "name": "Captions",
        "from_frame": 0,
        "duration_frames": duration_frames,
        "transform": _default_transform(),
        "animations": _default_animation(),
        "style": {"preset": "karaoke", "safe_zone": True},
        "enabled": True,
        "locked": False,
        "automation": {"source": "clipper-auto", "editable": True},
    }
    hook_item = {
        "id": _item_id(candidate.id, "text", "hook"),
        "clip_id": candidate.id,
        "track_id": _track_id(candidate.id, "graphics"),
        "type": "text",
        "name": "Hook",
        "text": candidate.title or "",
        "from_frame": 0,
        "duration_frames": min(duration_frames, _seconds_to_frames(2.8, fps)),
        "transform": _default_transform(),
        "animations": {
            "opacity": [
                {"frame": 0, "value": 0.0, "easing": "ease-out"},
                {"frame": min(5, duration_frames - 1), "value": 1.0, "easing": "ease-out"},
            ],
            "scale": [
                {"frame": 0, "value": 0.96, "easing": "spring"},
                {"frame": min(8, duration_frames - 1), "value": 1.0, "easing": "spring"},
            ],
        },
        "style": {"role": "hook", "safe_zone": True},
        "enabled": True,
        "locked": False,
        "automation": {"source": "clipper-auto", "editable": True},
    }
    return composition, tracks, [source_item, captions_item, hook_item]


def _caption_document(candidate: ClipCandidate) -> dict[str, Any]:
    """Create a timing-safe fallback document until word timestamps are attached.

    The canonical transcript pipeline may later replace this fallback with true word
    timing. Keeping a document in every V3 plan means the editor never needs to
    special-case a missing captions object.
    """
    text = str(candidate.transcript or "").strip()
    duration_ms = max(1, int(round(candidate.duration * 1000)))
    words = text.split()
    tokens: list[dict[str, Any]] = []
    if words:
        step = duration_ms / len(words)
        for index, word in enumerate(words):
            start = int(round(index * step))
            end = int(round((index + 1) * step))
            tokens.append(
                {
                    "id": f"{candidate.id}:word:{index}",
                    "text": word,
                    "start_ms": start,
                    "end_ms": max(start + 1, end),
                    "confidence": None,
                    "source": "fallback-even-spacing",
                }
            )
    return {
        "id": f"captions:{candidate.id}",
        "clip_id": candidate.id,
        "language": None,
        "text": text,
        "tokens": tokens,
        "pages": [],
        "source": "clipper-transcript",
        "timing_quality": "fallback" if tokens else "empty",
    }


def _scene_graph_for_candidates(candidates: list[ClipCandidate], ratios: list[str]) -> dict[str, Any]:
    compositions: dict[str, Any] = {}
    tracks: dict[str, Any] = {}
    items: dict[str, Any] = {}
    captions: dict[str, Any] = {}
    fps = DEFAULT_FPS
    for candidate in candidates:
        composition, candidate_tracks, candidate_items = _make_scene_graph_item(candidate, ratios, fps)
        compositions[candidate.id] = composition
        for track in candidate_tracks:
            tracks[track["id"]] = track
        for item in candidate_items:
            items[item["id"]] = item
        captions[f"captions:{candidate.id}"] = _caption_document(candidate)
    return {
        "schema": "clipper.scene-graph.v1",
        "assets": {
            "source:primary": {
                "id": "source:primary",
                "type": "video",
                "role": "source",
                "name": "Primary source",
                "path": None,
                "storage_path": None,
                "remote_url": None,
                "metadata": {},
            }
        },
        "compositions": compositions,
        "tracks": tracks,
        "items": items,
        "caption_documents": captions,
        "transitions": {},
        "templates": {"active": "clean-talking-head", "overrides": {}},
        "render": {
            "preferred_backend": "auto",
            "draft_backend": "browser",
            "final_backends": ["ffmpeg", "remotion"],
            "quality": "delivery",
        },
    }


def generate_edit_plan(
    project_id: str,
    candidates: list[ClipCandidate],
    ratios: list[str],
    brand: BrandKit,
    *,
    alternate_visual_layouts: bool = False,
    music_path: str | None = None,
) -> dict:
    ratios = normalize_ratios(ratios or ["9:16"])
    layout_modes = ["auto", "split", "pip", "interrupt"] if alternate_visual_layouts else ["auto"]
    plan = {
        "version": PLAN_VERSION,
        "schema": "clipper.edit-plan.v3",
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
            "transition_preset": "cut",
            "template": "clean-talking-head",
        },
        # V2-compatible clip envelopes remain the authoritative source-selection
        # contract for the Python auto pipeline. The V3 graph below is the visual
        # editing contract used by the Remotion editor.
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
        "scene_graph": _scene_graph_for_candidates(candidates, ratios),
        "revision": {
            "id": "initial",
            "parent_id": None,
            "sequence": 0,
            "message": "Initial automatic edit",
            "created_by": "clipper-auto",
        },
    }
    return plan


def _normalize_keyframes(raw: object, duration_frames: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, Any]] = []
    seen_frames: set[int] = set()
    for value in raw:
        if not isinstance(value, dict) or "value" not in value:
            continue
        try:
            frame = max(0, min(duration_frames - 1, int(value.get("frame", 0))))
        except (TypeError, ValueError):
            continue
        if frame in seen_frames:
            cleaned = [entry for entry in cleaned if entry["frame"] != frame]
        seen_frames.add(frame)
        easing = str(value.get("easing") or "linear")
        if easing not in {"linear", "ease-in", "ease-out", "ease-in-out", "spring", "step"}:
            easing = "linear"
        cleaned.append({"frame": frame, "value": value.get("value"), "easing": easing})
    cleaned.sort(key=lambda entry: entry["frame"])
    return cleaned


def _normalize_transform(raw: object) -> dict[str, Any]:
    base = _default_transform()
    if not isinstance(raw, dict):
        return base
    for key in ("x", "y", "scale_x", "scale_y", "rotation", "opacity", "anchor_x", "anchor_y", "border_radius"):
        if key in raw:
            try:
                base[key] = float(raw[key])
            except (TypeError, ValueError):
                pass
    base["opacity"] = max(0.0, min(1.0, float(base["opacity"])))
    base["scale_x"] = max(0.01, float(base["scale_x"]))
    base["scale_y"] = max(0.01, float(base["scale_y"]))
    base["anchor_x"] = max(0.0, min(1.0, float(base["anchor_x"])))
    base["anchor_y"] = max(0.0, min(1.0, float(base["anchor_y"])))
    crop = raw.get("crop")
    if isinstance(crop, dict):
        base["crop"] = {}
        for key in ("left", "right", "top", "bottom"):
            try:
                base["crop"][key] = max(0.0, min(0.95, float(crop.get(key, 0))))
            except (TypeError, ValueError):
                base["crop"][key] = 0.0
    return base


def _ensure_scene_graph(plan: dict) -> dict[str, Any]:
    graph = plan.get("scene_graph")
    if not isinstance(graph, dict):
        candidates = [candidate_from_plan(value) for value in plan.get("clips") or []]
        ratios: list[str] = []
        for item in plan.get("clips") or []:
            for ratio in normalize_ratios(item.get("ratios") or ["9:16"]):
                if ratio not in ratios:
                    ratios.append(ratio)
        graph = _scene_graph_for_candidates(candidates, ratios or ["9:16"])
    graph.setdefault("schema", "clipper.scene-graph.v1")
    for key, fallback in (
        ("assets", {}),
        ("compositions", {}),
        ("tracks", {}),
        ("items", {}),
        ("caption_documents", {}),
        ("transitions", {}),
        ("templates", {"active": "clean-talking-head", "overrides": {}}),
        ("render", {"preferred_backend": "auto", "draft_backend": "browser", "final_backends": ["ffmpeg", "remotion"]}),
    ):
        if not isinstance(graph.get(key), type(fallback)):
            graph[key] = deepcopy(fallback)
        else:
            graph.setdefault(key, deepcopy(fallback))
    return graph


def _validate_scene_graph(plan: dict) -> None:
    graph = _ensure_scene_graph(plan)
    compositions = graph["compositions"]
    tracks = graph["tracks"]
    items = graph["items"]
    assets = graph["assets"]

    for composition_id, composition in list(compositions.items()):
        if not isinstance(composition, dict):
            del compositions[composition_id]
            continue
        fps = int(composition.get("fps") or DEFAULT_FPS)
        if fps <= 0 or fps > 240:
            fps = DEFAULT_FPS
        composition["fps"] = fps
        composition["width"] = max(2, int(composition.get("width") or 1080))
        composition["height"] = max(2, int(composition.get("height") or 1920))
        composition["duration_frames"] = max(1, int(composition.get("duration_frames") or 1))
        composition["ratio_variants"] = normalize_ratios(composition.get("ratio_variants") or ["9:16"])

    for track_id, track in list(tracks.items()):
        if not isinstance(track, dict) or not track.get("clip_id"):
            del tracks[track_id]
            continue
        track["id"] = str(track_id)
        track["hidden"] = bool(track.get("hidden", False))
        track["muted"] = bool(track.get("muted", False))
        track["locked"] = bool(track.get("locked", False))
        try:
            track["z_index"] = int(track.get("z_index", 0))
        except (TypeError, ValueError):
            track["z_index"] = 0

    allowed_item_types = {"video", "audio", "image", "gif", "text", "solid", "captions", "shape", "lottie", "rive", "three", "sfx"}
    for item_id, item in list(items.items()):
        if not isinstance(item, dict):
            del items[item_id]
            continue
        clip_id = str(item.get("clip_id") or "")
        composition = compositions.get(clip_id)
        if not composition:
            del items[item_id]
            continue
        item_type = str(item.get("type") or "")
        if item_type not in allowed_item_types:
            del items[item_id]
            continue
        item["id"] = str(item_id)
        item["clip_id"] = clip_id
        duration = int(composition["duration_frames"])
        item["from_frame"] = max(0, min(duration - 1, int(item.get("from_frame", 0))))
        item["duration_frames"] = max(1, min(duration - item["from_frame"], int(item.get("duration_frames", duration))))
        item["enabled"] = bool(item.get("enabled", True))
        item["locked"] = bool(item.get("locked", False))
        item["transform"] = _normalize_transform(item.get("transform"))
        animations = item.get("animations") if isinstance(item.get("animations"), dict) else {}
        item["animations"] = {
            str(prop): _normalize_keyframes(values, duration)
            for prop, values in animations.items()
            if isinstance(values, list)
        }
        asset_id = item.get("asset_id")
        if asset_id is not None and str(asset_id) not in assets:
            # Missing assets are kept as logical references so a hosted editor can
            # hydrate them from Firebase project metadata. Mark them explicitly.
            item["asset_missing"] = True
        else:
            item.pop("asset_missing", None)

    plan["scene_graph"] = graph


def validate_edit_plan(plan: dict) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("Edit plan must be a JSON object")
    version = int(plan.get("version", 0))
    if version not in SUPPORTED_PLAN_VERSIONS:
        raise ValueError(f"Unsupported edit plan version: {plan.get('version')}")
    # V1/V2 are upgraded in memory; saves always write V3.
    plan["version"] = PLAN_VERSION
    plan["schema"] = "clipper.edit-plan.v3"
    plan["brand"] = normalize_brand(plan.get("brand")).to_dict()
    defaults = dict(plan.get("defaults") or {})
    defaults.setdefault("transition_preset", "cut")
    defaults.setdefault("template", "clean-talking-head")
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
    revision = plan.get("revision") if isinstance(plan.get("revision"), dict) else {}
    plan["revision"] = {
        "id": str(revision.get("id") or "initial")[:120],
        "parent_id": str(revision.get("parent_id"))[:120] if revision.get("parent_id") else None,
        "sequence": max(0, int(revision.get("sequence") or 0)),
        "message": str(revision.get("message") or "Edit plan")[:240],
        "created_by": str(revision.get("created_by") or "clipper")[:120],
    }
    _validate_scene_graph(plan)
    return plan


def create_revision(plan: dict, *, revision_id: str, message: str, created_by: str = "user") -> dict:
    clean = validate_edit_plan(deepcopy(plan))
    previous = dict(clean.get("revision") or {})
    clean["revision"] = {
        "id": str(revision_id)[:120],
        "parent_id": previous.get("id"),
        "sequence": int(previous.get("sequence") or 0) + 1,
        "message": str(message or "Edit")[:240],
        "created_by": str(created_by or "user")[:120],
    }
    return clean


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

    # Mirror persisted brand/music assets into the V3 graph without removing the
    # V2 compatibility fields consumed by the current FFmpeg renderer.
    graph = clean.get("scene_graph") or {}
    assets = graph.get("assets") if isinstance(graph.get("assets"), dict) else {}
    if clean["music_path"]:
        assets["audio:music"] = {
            "id": "audio:music",
            "type": "audio",
            "role": "music",
            "name": "Music bed",
            "path": clean["music_path"],
            "storage_path": None,
            "remote_url": None,
            "metadata": {},
        }
    logo_path = brand.get("logo_path")
    if logo_path:
        assets["image:logo"] = {
            "id": "image:logo",
            "type": "image",
            "role": "logo",
            "name": "Brand logo",
            "path": logo_path,
            "storage_path": None,
            "remote_url": None,
            "metadata": {},
        }
    graph["assets"] = assets
    clean["scene_graph"] = graph

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
