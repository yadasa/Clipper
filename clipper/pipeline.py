from __future__ import annotations

import json
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from .analysis import gemini_rerank, plan_visual_cues, rank_clips
from .audio import has_audio
from .automation import (
    AutomationLedger,
    auto_edit_profile,
    build_clean_master,
    choose_authoritative_audio,
)
from .brand import load_brand, normalize_brand
from .cache import StageCache, file_fingerprint, stable_hash
from .coherence import select_auto_clips
from .config import Settings, normalize_ratios
from .edit_plan import candidate_from_plan, generate_edit_plan, load_edit_plan, save_edit_plan
from .hooks import generate_hook
from .media import duration as media_duration, ingest
from .metadata import extract_thumbnail, generate_social_metadata
from .models import ClipCandidate, ProjectManifest, RenderedVariant, Transcript, Word
from .motion import apply_punch_ins, plan_punch_ins
from .multicam import build_multicam_master, replace_audio_with_synced_track
from .quality import check_render
from .render import align_visual_cues, render_clip, render_variants
from .smartcut import KeepInterval, build_keep_intervals, compact_duration, prepare_compacted_clip, remap_words
from .sync import estimate_sync, waveform_sync
from .transcript_io import load_transcript, save_transcript, transcript_from_dict, transcript_to_dict
from .transcription import transcribe
from .visuals import resolve_visuals

RENDER_CACHE_SCHEMA = 6
PREPARED_CACHE_SCHEMA = 3


def _dump_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(path)


def _project_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _is_disposable_staged_input(value: str, settings: Settings) -> bool:
    if value.startswith("http://") or value.startswith("https://"):
        return False
    try:
        path = Path(value).expanduser().resolve()
    except OSError:
        return False
    for staging_root in (settings.workdir / "incoming", settings.workdir / "firebase_inbox"):
        try:
            path.relative_to(staging_root.resolve())
            return True
        except ValueError:
            continue
    return False


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


def _candidate_ranges(candidate: ClipCandidate) -> list[tuple[float, float]]:
    if candidate.source_intervals:
        return [
            (float(item.get("start", 0)), float(item.get("end", 0)))
            for item in candidate.source_intervals
            if float(item.get("end", 0)) > float(item.get("start", 0))
        ]
    return [(candidate.start, candidate.end)]


def _absolute_words(words: list[Word], candidate: ClipCandidate) -> list[Word]:
    ranges = _candidate_ranges(candidate)
    return [
        word
        for word in words
        if any(word.end > start and word.start < end for start, end in ranges)
    ]


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
        source_intervals=[dict(item) for item in candidate.source_intervals],
    )


def _clamp_candidate_to_media(candidate: ClipCandidate, source_duration: float) -> ClipCandidate:
    if source_duration <= 0:
        return candidate
    start = max(0.0, min(float(candidate.start), source_duration))
    end = max(0.0, min(float(candidate.end), source_duration))
    intervals: list[dict[str, float]] = []
    for item in candidate.source_intervals:
        item_start = max(0.0, min(float(item.get("start", 0)), source_duration))
        item_end = max(0.0, min(float(item.get("end", 0)), source_duration))
        if item_end - item_start >= 0.05:
            intervals.append({"start": item_start, "end": item_end})
    if candidate.source_intervals and not intervals:
        raise ValueError(f"All stitched ranges for clip {candidate.id!r} are outside the render source")
    if intervals:
        start, end = intervals[0]["start"], intervals[-1]["end"]
    if end - start < 0.05:
        raise ValueError(
            f"Clip {candidate.id!r} is outside the render source ({source_duration:.3f}s): "
            f"{candidate.start:.3f}..{candidate.end:.3f}"
        )
    return ClipCandidate(
        id=candidate.id,
        start=start,
        end=end,
        score=candidate.score,
        title=candidate.title,
        reason=candidate.reason,
        transcript=candidate.transcript,
        metrics=dict(candidate.metrics),
        source_intervals=intervals,
    )


def _intervals_change_timeline(candidate: ClipCandidate, intervals: list[KeepInterval]) -> bool:
    expected = _candidate_ranges(candidate)
    if len(expected) != len(intervals):
        return True
    return any(
        abs(interval.start - start) > 0.001 or abs(interval.end - end) > 0.001
        for interval, (start, end) in zip(intervals, expected)
    ) or len(expected) > 1


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
    """Build one local clip timeline from continuous or stitched source ranges."""
    smart_cut = bool(item.get("smart_cut", True))
    punch_enabled = bool(item.get("punch_ins", True))
    remove_fillers = bool(item.get("remove_fillers", True))
    base_ranges = _candidate_ranges(candidate)

    intervals: list[KeepInterval] = []
    if smart_cut:
        for range_index, (start, end) in enumerate(base_ranges):
            local = ClipCandidate(
                id=f"{candidate.id}_part_{range_index}",
                start=start,
                end=end,
                score=candidate.score,
                title=candidate.title,
                transcript=candidate.transcript,
            )
            intervals.extend(
                build_keep_intervals(local, transcript_words, remove_fillers=remove_fillers)
            )
    else:
        intervals = [KeepInterval(start, end) for start, end in base_ranges]

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


def _quality_gate(
    variants: list[RenderedVariant],
    source: Path,
    candidate: ClipCandidate,
    words: list[Word],
    brand,
    caption_preset: str | None,
    hook_text: str | None,
    music_path: str | None,
    root: Path,
) -> tuple[list[RenderedVariant], list[dict]]:
    """Validate delivery files and retry a broken variant with a simpler visual graph."""
    expect_audio = has_audio(source) or bool(music_path)
    checks: list[dict] = []
    fixed: list[RenderedVariant] = []
    for variant in variants:
        check = check_render(variant, expected_duration=candidate.duration, expect_audio=expect_audio)
        if not check.ok:
            # B-roll/overlay complexity is the most common optional render failure.
            # Retry the same source/captions/brand without optional visual inserts.
            variant = render_clip(
                source,
                candidate,
                words,
                [],
                variant.path,
                ratio=variant.aspect_ratio,
                layout_mode="auto",
                brand=brand,
                caption_preset=caption_preset,
                hook_text=hook_text,
                music_path=music_path,
                cues_aligned=True,
            )
            retry = check_render(variant, expected_duration=candidate.duration, expect_audio=expect_audio)
            checks.append({"initial": check.to_dict(), "fallback": retry.to_dict()})
            if not retry.ok:
                raise RuntimeError(
                    f"Delivery QA failed for {candidate.id} {variant.aspect_ratio}: {', '.join(retry.problems)}"
                )
        else:
            checks.append({"initial": check.to_dict(), "fallback": None})
        fixed.append(variant)
    _dump_json(root / "qa" / f"{candidate.id}.json", checks)
    return fixed, checks


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

        item_settings = replace(settings)
        cue_limit = item.get("broll_max_cues")
        if cue_limit is not None:
            item_settings.broll_max_cues = max(1, int(cue_limit))

        hook_text = None
        if item.get("hook_overlay", True):
            hook_text = str(item.get("hook_text") or "").strip() or generate_hook(local_candidate, item_settings)

        cues = plan_visual_cues(local_candidate, item_settings, words=words)
        cues = align_visual_cues(cues, words, local_candidate)
        cues = resolve_visuals(cues, root / "visuals" / candidate.id, item_settings)
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
            if settings.auto_mode and settings.auto_quality_gate:
                variants, _ = _quality_gate(
                    variants,
                    source,
                    local_candidate,
                    words,
                    brand,
                    item.get("caption_preset"),
                    hook_text,
                    music_path,
                    root,
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

        social_metadata = generate_social_metadata(local_candidate, item_settings)
        finished.append({
            "candidate": asdict(candidate),
            "render_candidate": asdict(local_candidate),
            "smart_cut_intervals": intervals,
            "punch_ins": punch_events,
            "hook_text": hook_text,
            "visual_cues": [asdict(cue) for cue in cues],
            "auto_profile": dict(item.get("auto_profile") or {}),
            "social_metadata": social_metadata,
            "variants": [asdict(variant) for variant in variants],
        })
        manifest.clips = finished
        _dump_json(root / "manifest.json", manifest.to_dict())

    manifest.clips = finished
    return manifest


def _refine_sync_if_needed(
    primary_path: Path,
    secondary_path: Path,
    initial,
    primary_transcript: Transcript,
    settings: Settings,
    cache: StageCache,
):
    if initial.confidence >= settings.auto_sync_refine_confidence:
        return initial, None
    secondary_transcript = _cached_transcribe(secondary_path, settings, cache)
    refined = estimate_sync(primary_path, secondary_path, primary_transcript, secondary_transcript)
    return refined, secondary_transcript


def _build_auto_plan(
    project_id: str,
    candidates: list[ClipCandidate],
    ratios: list[str],
    settings: Settings,
    alternate_visual_layouts: bool,
):
    brand = load_brand(settings.brand_kit_path or None)
    plan = generate_edit_plan(
        project_id,
        candidates,
        ratios,
        brand,
        alternate_visual_layouts=alternate_visual_layouts,
        music_path=settings.music_path or None,
    )
    plan["automation_mode"] = "auto"
    plan["pipeline_order"] = [
        "ingest_raw",
        "sync_precomp",
        "transcribe_authoritative_precomp",
        "global_silence_cleanup",
        "coherent_slice_selection",
        "automatic_edit_decisions",
        "visual_audio_render",
        "captions_final_filter",
        "delivery_quality_gate",
        "thumbnail_and_metadata",
    ]
    # Global cleanup already happened before selection. Do not tighten the same
    # source again independently inside every selected clip.
    plan["defaults"].update({
        "smart_cut": False,
        "remove_fillers": False,
        "punch_ins": settings.punch_ins,
        "hook_overlay": settings.hook_overlay,
    })
    for item, candidate in zip(plan["clips"], candidates):
        profile = auto_edit_profile(candidate, settings) if settings.auto_visual_intensity else {
            "broll_max_cues": settings.broll_max_cues,
            "punch_ins": settings.punch_ins,
            "hook_overlay": settings.hook_overlay,
            "caption_preset": settings.caption_preset,
        }
        item["smart_cut"] = False
        item["remove_fillers"] = False
        item["punch_ins"] = bool(profile["punch_ins"])
        item["hook_overlay"] = bool(profile["hook_overlay"])
        item["caption_preset"] = str(profile["caption_preset"])
        item["broll_max_cues"] = int(profile["broll_max_cues"])
        item["auto_profile"] = dict(profile)
    return plan


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
    mode = "auto" if settings.auto_mode else "manual"
    ledger = AutomationLedger(root / "auto_stages.json", mode=mode)

    manifest = ProjectManifest(
        project_id=project_id,
        source_path="",
        source_name=Path(source).name or source,
        created_at=datetime.now(timezone.utc).isoformat(),
        ratios=ratios,
        automation_mode=mode,
        stage_report_path=str(root / "auto_stages.json"),
        status="ingesting",
        hardware_profile=profile.to_dict() if profile else {},
    )
    manifest_path = root / "manifest.json"
    _dump_json(manifest_path, manifest.to_dict())

    def hardlink_ok(value: str) -> bool:
        return bool(prefer_hardlink_ingest or _is_disposable_staged_input(value, settings))

    current_stage = "ingest_raw"
    try:
        ledger.start(current_stage, automation_mode=mode)
        primary = ingest(
            source,
            source_dir,
            own_content_ack=own_content_ack,
            prefer_hardlink=hardlink_ok(source),
        )
        manifest.source_path = str(primary)
        manifest.source_name = primary.name

        audio_path: Path | None = None
        if external_audio:
            audio_path = ingest(
                external_audio,
                root / "external_audio",
                own_content_ack=own_content_ack,
                prefer_hardlink=hardlink_ok(external_audio),
            )
        camera_paths: list[Path] = []
        for index, secondary in enumerate(secondary_cameras or []):
            camera_paths.append(ingest(
                secondary,
                root / "cameras" / f"camera_{index + 2}",
                own_content_ack=own_content_ack,
                prefer_hardlink=hardlink_ok(secondary),
            ))
        ledger.complete(current_stage, cameras=len(camera_paths), external_audio=bool(audio_path))

        if settings.auto_mode:
            current_stage = "sync_precomp"
            manifest.status = "syncing"
            _dump_json(manifest_path, manifest.to_dict())
            ledger.start(current_stage)
            working_primary = primary
            sync_summary: dict[str, object] = {"cameras": []}

            selected_audio, audio_decision = choose_authoritative_audio(primary, audio_path)
            sync_summary["audio_quality"] = audio_decision
            if audio_path is not None and Path(selected_audio) == audio_path:
                initial_audio_sync = waveform_sync(primary, audio_path)
                # Low-confidence audio sync gets a transcript refinement before
                # we create the authoritative precomp.
                if initial_audio_sync.confidence < settings.auto_sync_refine_confidence:
                    primary_sync_transcript = _cached_transcribe(primary, settings, cache)
                    audio_sync_transcript = _cached_transcribe(audio_path, settings, cache)
                    audio_sync = estimate_sync(
                        primary,
                        audio_path,
                        primary_sync_transcript,
                        audio_sync_transcript,
                    )
                else:
                    audio_sync = initial_audio_sync
                _dump_json(root / "sync" / "external_audio.json", asdict(audio_sync))
                synced = root / "source" / "precomp-synced-audio.mp4"
                if not synced.is_file() or synced.stat().st_size < 10_000:
                    working_primary = replace_audio_with_synced_track(primary, audio_path, audio_sync, synced)
                else:
                    working_primary = synced
                sync_summary["external_audio"] = asdict(audio_sync)
            else:
                sync_summary["external_audio"] = None

            # Cheap waveform passes happen before semantic analysis. Weak camera
            # matches are refined after the authoritative precomp is transcribed.
            camera_syncs = [waveform_sync(primary, path) for path in camera_paths]
            ledger.complete(current_stage, **sync_summary)

            current_stage = "transcribe_authoritative_precomp"
            manifest.status = "transcribing"
            _dump_json(manifest_path, manifest.to_dict())
            ledger.start(current_stage, source=str(working_primary))
            primary_transcript = _cached_transcribe(working_primary, settings, cache)
            save_transcript(primary_transcript, root / "transcript_precomp.json")
            ledger.complete(current_stage, language=primary_transcript.language, duration=primary_transcript.duration)

            refined_cameras = []
            for camera_path, initial in zip(camera_paths, camera_syncs):
                sync, _ = _refine_sync_if_needed(
                    working_primary,
                    camera_path,
                    initial,
                    primary_transcript,
                    settings,
                    cache,
                )
                _dump_json(root / "sync" / f"camera_{len(refined_cameras) + 2}.json", asdict(sync))
                refined_cameras.append((camera_path, sync))
            if refined_cameras:
                multicam = root / "source" / "precomp-multicam.mp4"
                if not multicam.is_file() or multicam.stat().st_size < 10_000:
                    working_primary = build_multicam_master(
                        working_primary,
                        refined_cameras,
                        primary_transcript,
                        multicam,
                    )
                else:
                    working_primary = multicam

            current_stage = "global_silence_cleanup"
            manifest.status = "cleaning"
            _dump_json(manifest_path, manifest.to_dict())
            ledger.start(current_stage)
            clean_master = root / "source" / "precomp-clean-master.mp4"
            working_primary, final_transcript, master_intervals = build_clean_master(
                working_primary,
                primary_transcript,
                clean_master,
                settings,
            )
            transcript_path = root / "transcript.json"
            save_transcript(final_transcript, transcript_path)
            manifest.transcript_path = str(transcript_path)
            manifest.render_source_path = str(working_primary)
            _dump_json(root / "source" / "clean-master-edl.json", master_intervals)
            ledger.complete(
                current_stage,
                before_seconds=round(primary_transcript.duration, 3),
                after_seconds=round(final_transcript.duration, 3),
                kept_ranges=len(master_intervals),
            )

            current_stage = "coherent_slice_selection"
            manifest.status = "selecting"
            _dump_json(manifest_path, manifest.to_dict())
            ledger.start(current_stage)
            candidates = select_auto_clips(final_transcript, settings)
            if not candidates:
                candidates = rank_clips(final_transcript, settings)
            candidates = gemini_rerank(candidates, settings)
            _dump_json(root / "clip_candidates.json", [asdict(candidate) for candidate in candidates])
            ledger.complete(
                current_stage,
                clips=len(candidates),
                stitched=sum(1 for candidate in candidates if candidate.source_intervals),
            )

            current_stage = "automatic_edit_decisions"
            ledger.start(current_stage)
            plan = _build_auto_plan(
                project_id,
                candidates,
                ratios,
                settings,
                alternate_visual_layouts,
            )
            edit_plan_path = root / "edit_plan.json"
            save_edit_plan(plan, edit_plan_path)
            manifest.edit_plan_path = str(edit_plan_path)
            ledger.complete(current_stage, clips=len(plan.get("clips") or []))
        else:
            # Manual mode preserves the older direct path and its explicit knobs.
            current_stage = "transcribe"
            ledger.start(current_stage)
            primary_transcript = _cached_transcribe(primary, settings, cache)
            working_primary = primary
            if audio_path:
                audio_transcript = _cached_transcribe(audio_path, settings, cache)
                sync = estimate_sync(primary, audio_path, primary_transcript, audio_transcript)
                _dump_json(root / "sync" / "external_audio.json", asdict(sync))
                synced = root / "source" / "source-synced-audio.mp4"
                working_primary = replace_audio_with_synced_track(primary, audio_path, sync, synced)
            synced_cameras = []
            for index, camera_path in enumerate(camera_paths):
                sec_transcript = _cached_transcribe(camera_path, settings, cache)
                sync = estimate_sync(primary, camera_path, primary_transcript, sec_transcript)
                _dump_json(root / "sync" / f"camera_{index + 2}.json", asdict(sync))
                synced_cameras.append((camera_path, sync))
            if synced_cameras:
                multicam = root / "source" / "multicam-master.mp4"
                working_primary = build_multicam_master(working_primary, synced_cameras, primary_transcript, multicam)
            transcript_path = root / "transcript.json"
            save_transcript(primary_transcript, transcript_path)
            manifest.transcript_path = str(transcript_path)
            manifest.render_source_path = str(working_primary)
            ledger.complete(current_stage)

            current_stage = "selecting"
            ledger.start(current_stage)
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
            final_transcript = primary_transcript
            ledger.complete(current_stage, clips=len(candidates))

        current_stage = "visual_audio_render_then_captions"
        manifest.status = "rendering"
        _dump_json(manifest_path, manifest.to_dict())
        ledger.start(
            current_stage,
            captions_final_filter=True,
            note="Captions are composed after crop, B-roll, logo, hook, and visual overlays in the final FFmpeg graph",
        )
        manifest = _render_plan(
            manifest,
            final_transcript,
            Path(manifest.render_source_path or primary),
            plan,
            root,
            settings,
        )
        ledger.complete(current_stage, clips=len(manifest.clips))

        current_stage = "thumbnail_and_metadata"
        ledger.start(current_stage)
        # Thumbnail/social metadata are generated inside _render_plan after the
        # final captioned delivery file exists; this ledger entry makes ordering explicit.
        ledger.complete(current_stage, clips=len(manifest.clips))

        manifest.status = "done"
        manifest.error = None
        _dump_json(manifest_path, manifest.to_dict())
        return manifest
    except Exception as exc:
        ledger.fail(current_stage, exc)
        manifest.status = "failed"
        manifest.error = str(exc)
        _dump_json(manifest_path, manifest.to_dict())
        raise


def rerender_project(project: str | Path, settings: Settings | None = None) -> ProjectManifest:
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