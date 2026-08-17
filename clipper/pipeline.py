from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .analysis import gemini_rerank, plan_visual_cues, rank_clips
from .config import Settings, normalize_ratios
from .media import ingest
from .models import ProjectManifest
from .multicam import build_multicam_master, replace_audio_with_synced_track
from .render import render_variants
from .sync import estimate_sync
from .transcription import transcribe
from .visuals import resolve_visuals


def _dump_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _project_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def process_video(
    source: str,
    *,
    ratios: list[str] | None = None,
    own_content_ack: bool = False,
    settings: Settings | None = None,
    secondary_cameras: list[str] | None = None,
    external_audio: str | None = None,
    alternate_visual_layouts: bool = True,
) -> ProjectManifest:
    settings = settings or Settings()
    settings.ensure_dirs()
    ratios = normalize_ratios(ratios)
    project_id = _project_id()
    root = settings.workdir / "projects" / project_id
    source_dir = root / "source"
    root.mkdir(parents=True, exist_ok=True)

    manifest = ProjectManifest(
        project_id=project_id,
        source_path="",
        source_name=Path(source).name or source,
        created_at=datetime.now(timezone.utc).isoformat(),
        ratios=ratios,
        status="ingesting",
    )
    manifest_path = root / "manifest.json"
    _dump_json(manifest_path, manifest.to_dict())

    try:
        primary = ingest(source, source_dir, own_content_ack=own_content_ack)
        manifest.source_path = str(primary)
        manifest.source_name = primary.name
        manifest.status = "transcribing"
        _dump_json(manifest_path, manifest.to_dict())

        primary_transcript = transcribe(primary, settings)
        transcript_payload = {
            "text": primary_transcript.text,
            "language": primary_transcript.language,
            "duration": primary_transcript.duration,
            "segments": [
                {
                    "start": s.start, "end": s.end, "text": s.text,
                    "words": [asdict(w) for w in s.words],
                }
                for s in primary_transcript.segments
            ],
        }
        transcript_path = root / "transcript.json"
        _dump_json(transcript_path, transcript_payload)
        manifest.transcript_path = str(transcript_path)

        # Optional separate microphone: estimate word/waveform alignment and replace camera audio.
        working_primary = primary
        if external_audio:
            audio_path = ingest(external_audio, root / "external_audio", own_content_ack=own_content_ack)
            audio_transcript = transcribe(audio_path, settings)
            sync = estimate_sync(primary, audio_path, primary_transcript, audio_transcript)
            _dump_json(root / "sync" / "external_audio.json", asdict(sync))
            synced = root / "source" / "source-synced-audio.mp4"
            working_primary = replace_audio_with_synced_track(primary, audio_path, sync, synced)

        # Optional secondary cameras: sync every recording onto primary transcript/audio time,
        # then generate a speech-boundary multicam master used by the downstream clipper.
        synced_cameras = []
        for index, secondary in enumerate(secondary_cameras or []):
            sec_path = ingest(secondary, root / "cameras" / f"camera_{index + 2}", own_content_ack=own_content_ack)
            sec_transcript = transcribe(sec_path, settings)
            sync = estimate_sync(primary, sec_path, primary_transcript, sec_transcript)
            _dump_json(root / "sync" / f"camera_{index + 2}.json", asdict(sync))
            synced_cameras.append((sec_path, sync))
        if synced_cameras:
            multicam = root / "source" / "multicam-master.mp4"
            working_primary = build_multicam_master(working_primary, synced_cameras, primary_transcript, multicam)

        manifest.status = "selecting"
        _dump_json(manifest_path, manifest.to_dict())
        candidates = gemini_rerank(rank_clips(primary_transcript, settings), settings)
        _dump_json(root / "clip_candidates.json", [asdict(c) for c in candidates])

        manifest.status = "rendering"
        _dump_json(manifest_path, manifest.to_dict())
        finished = []
        for candidate in candidates:
            cues = plan_visual_cues(candidate, settings)
            cues = resolve_visuals(cues, root / "visuals" / candidate.id, settings)
            _dump_json(root / "visuals" / candidate.id / "timeline.json", [asdict(c) for c in cues])
            modes = ["auto", "split", "pip", "interrupt"] if alternate_visual_layouts and any(c.asset_path for c in cues) else ["auto"]
            variants = render_variants(
                working_primary, candidate, primary_transcript.words, cues,
                root / "clips", ratios, layout_modes=modes,
            )
            finished.append({
                "candidate": asdict(candidate),
                "visual_cues": [asdict(c) for c in cues],
                "variants": [asdict(v) for v in variants],
            })
            # Persist after each clip so a long batch survives interruption.
            manifest.clips = finished
            _dump_json(manifest_path, manifest.to_dict())

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
