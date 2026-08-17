from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .analysis import gemini_rerank, plan_visual_cues, rank_clips
from .brand import load_brand, normalize_brand
from .cache import StageCache, file_fingerprint, stable_hash
from .config import Settings, normalize_ratios
from .edit_plan import candidate_from_plan, generate_edit_plan, load_edit_plan, save_edit_plan
from .hooks import generate_hook
from .media import duration as media_duration, ingest
from .metadata import extract_thumbnail, generate_social_metadata
from .models import ClipCandidate, ProjectManifest, RenderedVariant, Transcript, Word
from .motion import apply_punch_ins, plan_punch_ins
from .multicam import build_multicam_master, replace_audio_with_synced_track
from .render import align_visual_cues, render_variants
from .smartcut import KeepInterval, build_keep_intervals, compact_duration, prepare_compacted_clip, remap_words
from .sync import estimate_sync
from .transcript_io import load_transcript, save_transcript, transcript_from_dict, transcript_to_dict
from .transcription import transcribe
from .visuals import resolve_visuals

RENDER_CACHE_SCHEMA = 5
PREPARED_CACHE_SCHEMA = 2


def _dump_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(path)


def _project_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _cached_transcribe(path: str | Path, settings: Settings, cache: StageCache) -> Transcript:
    options = {
        "model": settings.whisper_model,
        "device": settings.whisper_device,
        "batch": settings.whisper_batch_size,
        "word_timestamps": True,
        "vad": True,
    }
    key = cache.key_for("transcript", path, options)
    if settings.stage_cache:
        hit = cache.load("transcript", key)
        if hit and isinstance(hit.get("transcript"), dict):
            return transcript_from_dict(hit["transcript"])
    result = transcribe(path, settings)
    if settings.stage_cache:
        cache.save("transcript", key, {"transcript": transcript_to_dict(result), "options": options})
    return result


def _absolute_words(words: list[Word], candidate: ClipCandidate) -> list[Word]:
    return [word for word in words if word.end > candidate.start and word.start < candidate.end]


def _candidate_with_current_text(candidate: ClipCandidate, words: list[Word]) -> ClipCandidate:
    selected = _absolute_words(words, candidate)
    text = " ".join(word.text for word in selected).strip() or candidate.transcript
    return ClipCandidate(
        id=candidate.id,
        start=candidate.start,
        end=candidate.end,
        score=candidate.score,
        title=candidate.title,
        reason=candidate.reason,
        transcript=text,
        metrics=dict(candidate.metrics),
    )


def _clamp_candidate_to_media(candidate: ClipCandidate, source_duration: float) -> ClipCandidate:
    """Keep edited clip ranges inside the actual render source timeline."""
    if source_duration <= 0:
        return candidate
    start = max(0.0, min(float(candidate.start), source_duration))
    end = max(0.0, min(float(candidate.end), source_duration))
    if end - start < 0.05:
        raise ValueError(
            f"Clip {candidate.id!r} is outside the render source ({source_duration:.3f}s): "
            f"{candidate.start:.3f}..{candidate.end:.3f}"
        )
    if abs(start - candidate.start) < 0.001 and abs(end - candidate.end) < 0.001:
        return candidate
    return ClipCandidate(
        id=candidate.id,
        start=start,
        end=end,
        score=candidate.score,
        title=candidate.title,
        reason=candidate.reason,
        transcript=candidate.transcript,
        metrics=dict(candidate.metrics),
    )


def _intervals_change_timeline(candidate: ClipCandidate, intervals: list[KeepInterval]) -> bool:
    if len(intervals) != 1:
        return True
    interval = intervals[0]
    return (
        abs(interval.start - candidate.start) > 0.001
        or abs(interval.end - candidate.end) > 0.001
    )


def _local_candidate_state(
    candidate: ClipCandidate,
    transcript_words: list[Word],
    intervals: list[KeepInterval],
) -> tuple[ClipCandidate, list[Word]]:
    mapped = remap_words(transcript_words, intervals)
    duration = compact_duration(intervals)
    text = " ".join(word.text for word in mapped).strip() or candidate.transcript
    local_candidate = ClipCandidate(
        id=candidate.id,
        start=0.0,
        end=duration,
        score=candidate.score,
        title=candidate.title,
        reason=candidate.reason,
        transcript=text,
        metrics=dict(candidate.metrics),
    )
    return local_candidate, mapped


def _prepare_candidate(
    render_source: Path,
    candidate: ClipCandidate,
    transcript_words: list[Word],
    item: dict,
    root: Path,
) -> tuple[Path, ClipCandidate, list[Word], list[dict], list[dict]]:
    """Apply jump cuts and emphasis motion, returning the correct render timeline.

    No intermediate is encoded unless the EDL actually removes time or a punch-in
    is actually scheduled. This avoids an unnecessary full clip encode for the
    common case where smart-cut analysis decides the original clip is already
    clean enough.
    """
    smart_cut = bool(item.get("smart_cut", True))
    punch_enabled = bool(item.get("punch_ins", True))
    remove_fillers = bool(item.get("remove_fillers", True))

    if smart_cut:
        intervals = build_keep_intervals(candidate, transcript_words, remove_fillers=remove_fillers)
    else:
        intervals = [KeepInterval(candidate.start, candidate.end)]

    interval_dicts = [asdict(interval) for interval in intervals]
    has_cuts = _intervals_change_timeline(candidate, intervals)
    if not has_cuts and not punch_enabled:
        return render_source, candidate, transcript_words, interval_dicts, []

    local_candidate, mapped = _local_candidate_state(candidate, transcript_words, intervals)
    events = plan_punch_ins(mapped, local_candidate.duration) if punch_enabled else []
    punch_dicts = [asdict(event) for event in events]

    if not has_cuts and not events:
        return render_source, candidate, transcript_words, interval_dicts, []

    prepared_dir = root / "prepared" / candidate.id
    prepared_dir.mkdir(parents=True, exist_ok=True)
    cut_signature = stable_hash({
        "schema": PREPARED_CACHE_SCHEMA,
        "source": file_fingerprint(render_source),
        "intervals": interval_dicts,
    })[:20]
    cut_path = prepared_dir / f"smartcut-{cut_signature}.mp4"
    if not cut_path.is_file() or cut_path.stat().st_size < 10_000:
        prepare_compacted_clip(render_source, intervals, cut_path)

    source = cut_path
    if events:
        punch_signature = stable_hash({
            "schema": PREPARED_CACHE_SCHEMA,
            "cut": cut_signature,
            "events": punch_dicts,
        })[:20]
        punch_path = prepared_dir / f"motion-{punch_signature}.mp4"
        if not punch_path.is_file() or punch_path.stat().st_size < 10_000:
            source = Path(apply_punch_ins(cut_path, events, punch_path))
        else:
            source = punch_path
        if source != punch_path or not punch_path.is_file():
            source = cut_path
            punch_dicts = []

    return source, local_candidate, mapped, interval_dicts, punch_dicts


def _asset_fingerprint(value: str | None) -> dict | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_file():
        return None
    try:
        return {
            "path": str(path.resolve()),
            "fingerprint": file_fingerprint(path, sample_bytes=256 * 1024),
        }
    except Exception:
        stat = path.stat()
        return {"path": str(path.resolve()), "size": stat.st_size, "mtime": stat.st_mtime_ns}


def _render_signature(
    source: Path,
    candidate: ClipCandidate,
    item: dict,
    cues: list,
    brand: dict,
    hook_text: str | None,
    music_path: str | None,
) -> str:
    cue_payload = []
    for cue in cues:
        cue_payload.append({
            "start": cue.start,
            "end": cue.end,
            "transcript": cue.transcript,
            "query": cue.query,
            "modes": cue.modes,
            "asset_type": cue.asset_type,
            "provider": cue.provider,
            "asset": _asset_fingerprint(cue.asset_path),
        })
    logo_path = str(brand.get("logo_path") or "") if isinstance(brand, dict) else ""
    return stable_hash({
        "schema": RENDER_CACHE_SCHEMA,
        "source": file_fingerprint(source),
        "candidate": asdict(candidate),
        "ratios": item.get("ratios"),
        "layout_modes": item.get("layout_modes"),
        "caption_preset": item.get("caption_preset"),
        "brand": brand,
        "logo": _asset_fingerprint(logo_path),
        "hook": hook_text,
        "music": _asset_fingerprint(music_path),
        "cues": cue_payload,
    })


def _load_cached_variants(path: Path, signature: str) -> list[RenderedVariant] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("signature") != signature:
        return None
    result = []
    for raw in data.get("variants") or []:
        artifact = Path(raw.get("path", ""))
        if not artifact.is_file() or artifact.stat().st_size < 10_000:
            return None
        result.append(RenderedVariant(**raw))
    return result or None


def _save_cached_variants(path: Path, signature: str, variants: list[RenderedVariant]) -> None:
    _dump_json(path, {"signature": signature, "variants": [asdict(variant) for variant in variants]})


def _resolve_project_asset(value: str | None, root: Path) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        project_relative = (root / path).resolve()
        if project_relative.is_file():
            return str(project_relative)
    return str(path) if path.is_file() else None


def _render_plan(
    manifest: ProjectManifest,
    transcript: Transcript,
    render_source: Path,
    plan: dict,
    root: Path,
    settings: Settings,
) -> ProjectManifest:
    brand = normalize_brand(plan.get("brand"))
    brand.logo_path = _resolve_project_asset(brand.logo_path, root)
    music_path = _resolve_project_asset(plan.get("music_path"), root)
    finished: list[dict] = []
    source_duration = media_duration(render_source)

    for item in plan.get("clips") or []:
        if not item.get("enabled", True):
            continue
        candidate = _clamp_candidate_to_media(candidate_from_plan(item), source_duration)
        candidate = _candidate_with_current_text(candidate, transcript.words)
        source, local_candidate, words, intervals, punch_events = _prepare_candidate(
            render_source,
            candidate,
            transcript.words,
            item,
            root,
        )

        hook_text = None
        if item.get("hook_overlay", True):
            hook_text = str(item.get("hook_text") or "").strip() or generate_hook(local_candidate, settings)

        cues = plan_visual_cues(local_candidate, settings, words=words)
        cues = align_visual_cues(cues, words, local_candidate)
        cues = resolve_visuals(cues, root / "visuals" / candidate.id, settings)
        _dump_json(root / "visuals" / candidate.id / "timeline.json", [asdict(cue) for cue in cues])

        ratios = normalize_ratios(item.get("ratios") or manifest.ratios)
        modes = [
            mode
            for mode in item.get("layout_modes") or ["auto"]
            if mode in {"auto", "split", "pip", "interrupt"}
        ]
        if not modes:
            modes = ["auto"]

        signature = _render_signature(
            source,
            local_candidate,
            item,
            cues,
            brand.to_dict(),
            hook_text,
            music_path,
        )
        cache_path = root / "clips" / candidate.id / "render-cache.json"
        variants = _load_cached_variants(cache_path, signature) if settings.stage_cache else None
        if variants is None:
            variants = render_variants(
                source,
                local_candidate,
                words,
                cues,
                root / "clips",
                ratios,
                layout_modes=modes,
                brand=brand,
                caption_preset=item.get("caption_preset"),
                hook_text=hook_text,
                music_path=music_path,
                cues_aligned=True,
            )
            if settings.stage_cache:
                _save_cached_variants(cache_path, signature, variants)

        for variant in variants:
            thumb = Path(variant.path).with_suffix(".jpg")
            if not thumb.is_file() or thumb.stat().st_size < 1_000:
                try:
                    extract_thumbnail(variant.path, thumb)
                except Exception:
                    pass
            if thumb.is_file():
                variant.thumbnail_path = str(thumb)

        social_metadata = generate_social_metadata(local_candidate, settings)
        finished.append({
            "candidate": asdict(candidate),
            "render_candidate": asdict(local_candidate),
            "smart_cut_intervals": intervals,
            "punch_ins": punch_events,
            "hook_text": hook_text,
            "visual_cues": [asdict(cue) for cue in cues],
            "social_metadata": social_metadata,
            "variants": [asdict(variant) for variant in variants],
        })
        manifest.clips = finished
        _dump_json(root / "manifest.json", manifest.to_dict())

    manifest.clips = finished
    return manifest


def process_video(
    source: str,
    *,
    ratios: list[str] | None = None,
    own_content_ack: bool = False,
    settings: Settings | None = None,
    secondary_cameras: list[str] | None = None,
    external_audio: str | None = None,
    alternate_visual_layouts: bool = False,
    prefer_hardlink_ingest: bool = False,
) -> ProjectManifest:
    settings = settings or Settings()
    settings.ensure_dirs()
    profile = settings.apply_hardware_profile()
    ratios = normalize_ratios(ratios)
    project_id = _project_id()
    root = settings.workdir / "projects" / project_id
    source_dir = root / "source"
    root.mkdir(parents=True, exist_ok=True)
    cache = StageCache(settings.workdir / "cache")

    manifest = ProjectManifest(
        project_id=project_id,
        source_path="",
        source_name=Path(source).name or source,
        created_at=datetime.now(timezone.utc).isoformat(),
        ratios=ratios,
        status="ingesting",
        hardware_profile=profile.to_dict() if profile else {},
    )
    manifest_path = root / "manifest.json"
    _dump_json(manifest_path, manifest.to_dict())

    try:
        primary = ingest(
            source,
            source_dir,
            own_content_ack=own_content_ack,
            prefer_hardlink=prefer_hardlink_ingest,
        )
        manifest.source_path = str(primary)
        manifest.source_name = primary.name
        manifest.status = "transcribing"
        _dump_json(manifest_path, manifest.to_dict())

        primary_transcript = _cached_transcribe(primary, settings, cache)
        transcript_path = root / "transcript.json"
        save_transcript(primary_transcript, transcript_path)
        manifest.transcript_path = str(transcript_path)

        working_primary = primary
        if external_audio:
            audio_path = ingest(
                external_audio,
                root / "external_audio",
                own_content_ack=own_content_ack,
                prefer_hardlink=prefer_hardlink_ingest,
            )
            audio_transcript = _cached_transcribe(audio_path, settings, cache)
            sync = estimate_sync(primary, audio_path, primary_transcript, audio_transcript)
            _dump_json(root / "sync" / "external_audio.json", asdict(sync))
            synced = root / "source" / "source-synced-audio.mp4"
            if not synced.is_file() or synced.stat().st_size < 10_000:
                working_primary = replace_audio_with_synced_track(primary, audio_path, sync, synced)
            else:
                working_primary = synced

        synced_cameras = []
        for index, secondary in enumerate(secondary_cameras or []):
            sec_path = ingest(
                secondary,
                root / "cameras" / f"camera_{index + 2}",
                own_content_ack=own_content_ack,
                prefer_hardlink=prefer_hardlink_ingest,
            )
            sec_transcript = _cached_transcribe(sec_path, settings, cache)
            sync = estimate_sync(primary, sec_path, primary_transcript, sec_transcript)
            _dump_json(root / "sync" / f"camera_{index + 2}.json", asdict(sync))
            synced_cameras.append((sec_path, sync))
        if synced_cameras:
            multicam = root / "source" / "multicam-master.mp4"
            if not multicam.is_file() or multicam.stat().st_size < 10_000:
                working_primary = build_multicam_master(working_primary, synced_cameras, primary_transcript, multicam)
            else:
                working_primary = multicam

        manifest.render_source_path = str(working_primary)
        manifest.status = "selecting"
        _dump_json(manifest_path, manifest.to_dict())
        candidates = gemini_rerank(rank_clips(primary_transcript, settings), settings)
        _dump_json(root / "clip_candidates.json", [asdict(candidate) for candidate in candidates])

        brand = load_brand(settings.brand_kit_path or None)
        plan = generate_edit_plan(
            project_id,
            candidates,
            ratios,
            brand,
            alternate_visual_layouts=alternate_visual_layouts,
            music_path=settings.music_path or None,
        )
        plan["defaults"].update({
            "smart_cut": settings.smart_cut,
            "remove_fillers": settings.remove_fillers,
            "punch_ins": settings.punch_ins,
            "hook_overlay": settings.hook_overlay,
        })
        for item in plan["clips"]:
            item["smart_cut"] = settings.smart_cut
            item["remove_fillers"] = settings.remove_fillers
            item["punch_ins"] = settings.punch_ins
            item["hook_overlay"] = settings.hook_overlay
        edit_plan_path = root / "edit_plan.json"
        save_edit_plan(plan, edit_plan_path)
        manifest.edit_plan_path = str(edit_plan_path)

        manifest.status = "rendering"
        _dump_json(manifest_path, manifest.to_dict())
        manifest = _render_plan(manifest, primary_transcript, Path(working_primary), plan, root, settings)
        manifest.status = "done"
        manifest.error = None
        _dump_json(manifest_path, manifest.to_dict())
        return manifest
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = str(exc)
        _dump_json(manifest_path, manifest.to_dict())
        raise


def rerender_project(project: str | Path, settings: Settings | None = None) -> ProjectManifest:
    """Rerender an existing project's editable plan without retranscribing."""
    settings = settings or Settings()
    settings.ensure_dirs()
    settings.apply_hardware_profile()
    root = Path(project)
    if root.is_file():
        root = root.parent
    manifest_path = root / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = ProjectManifest(**data)
    if not manifest.transcript_path or not Path(manifest.transcript_path).is_file():
        raise FileNotFoundError("Project transcript.json is missing")
    if not manifest.edit_plan_path or not Path(manifest.edit_plan_path).is_file():
        raise FileNotFoundError("Project edit_plan.json is missing")
    render_source = Path(manifest.render_source_path or manifest.source_path)
    if not render_source.is_file():
        raise FileNotFoundError(f"Project render source is missing: {render_source}")

    transcript = load_transcript(manifest.transcript_path)
    plan = load_edit_plan(manifest.edit_plan_path)
    manifest.status = "rendering"
    manifest.error = None
    _dump_json(manifest_path, manifest.to_dict())
    try:
        manifest = _render_plan(manifest, transcript, render_source, plan, root, settings)
        manifest.status = "done"
        _dump_json(manifest_path, manifest.to_dict())
        return manifest
    except Exception as exc:
        manifest.status = "failed"
        manifest.error = str(exc)
        _dump_json(manifest_path, manifest.to_dict())
        raise


def list_projects(settings: Settings | None = None) -> list[dict]:
    settings = settings or Settings()
    settings.ensure_dirs()
    rows = []
    for manifest in sorted((settings.workdir / "projects").glob("*/manifest.json"), reverse=True):
        try:
            rows.append(json.loads(manifest.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows
