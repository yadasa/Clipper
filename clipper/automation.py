from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings
from .models import ClipCandidate, Segment, Transcript, Word
from .smartcut import KeepInterval, build_keep_intervals, prepare_compacted_clip, remap_words


@dataclass(slots=True, frozen=True)
class AudioQuality:
    path: str
    score: float
    rms_dbfs: float
    peak_dbfs: float
    clipping_ratio: float
    active_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AutomationLedger:
    """Small durable stage ledger for the one-click pipeline.

    The manifest status is intentionally broad for compatibility. This report
    records the actual ordered auto stages so the web UI and diagnostics can say
    whether Clipper is syncing, cleaning, selecting, illustrating, rendering, or
    doing final QA rather than exposing one opaque "processing" state.
    """

    def __init__(self, path: str | Path, mode: str = "auto") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, Any] = {
            "version": 1,
            "mode": mode,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "current": None,
            "stages": [],
        }
        self._write()

    def _write(self) -> None:
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temp, self.path)

    def start(self, name: str, **details: Any) -> None:
        self.data["current"] = name
        self.data["stages"].append({
            "name": name,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "details": details,
        })
        self._write()

    def complete(self, name: str, **details: Any) -> None:
        for stage in reversed(self.data["stages"]):
            if stage.get("name") == name and stage.get("status") == "running":
                stage["status"] = "done"
                stage["completed_at"] = datetime.now(timezone.utc).isoformat()
                stage.setdefault("details", {}).update(details)
                break
        self.data["current"] = None
        self._write()

    def fail(self, name: str, error: Exception) -> None:
        for stage in reversed(self.data["stages"]):
            if stage.get("name") == name and stage.get("status") == "running":
                stage["status"] = "failed"
                stage["completed_at"] = datetime.now(timezone.utc).isoformat()
                stage["error"] = str(error)[:2000]
                break
        self.data["current"] = name
        self._write()


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(1e-8, value))


def measure_audio_quality(path: str | Path, *, seconds: float = 45.0) -> AudioQuality:
    """Cheaply score whether an audio track is usable as the authoritative precomp track.

    This is not a mastering score. It catches the failures that matter before an
    expensive transcription: near-silence, severe clipping, and an implausibly
    weak signal. The explicitly supplied mic still wins unless it is materially
    worse than camera audio.
    """
    cmd = [
        "ffmpeg", "-v", "error", "-t", f"{max(5.0, seconds):.1f}", "-i", str(path),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    except Exception:
        return AudioQuality(str(path), 0.0, -120.0, -120.0, 1.0, 0.0)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if pcm.size < 1600:
        return AudioQuality(str(path), 0.0, -120.0, -120.0, 1.0, 0.0)
    absolute = np.abs(pcm)
    rms = float(np.sqrt(np.mean(pcm * pcm) + 1e-12))
    peak = float(np.max(absolute))
    clipping = float(np.mean(absolute >= 0.985))
    active = float(np.mean(absolute >= 0.008))
    rms_db = _dbfs(rms)
    peak_db = _dbfs(peak)

    # Healthy dialogue commonly lands somewhere around -35..-10 dBFS RMS before
    # mastering. Avoid rewarding raw loudness past that useful range.
    if rms_db < -55:
        level = 5.0
    elif rms_db < -35:
        level = 45.0 + (rms_db + 55.0) * 2.0
    elif rms_db <= -12:
        level = 85.0
    else:
        level = max(55.0, 85.0 - (rms_db + 12.0) * 4.0)
    clip_penalty = min(70.0, clipping * 9000.0)
    activity = min(100.0, active * 220.0)
    score = level * 0.72 + activity * 0.28 - clip_penalty
    return AudioQuality(
        str(path),
        round(max(0.0, min(100.0, score)), 2),
        round(rms_db, 2),
        round(peak_db, 2),
        round(clipping, 6),
        round(active, 4),
    )


def choose_authoritative_audio(primary: str | Path, external: str | Path | None) -> tuple[str, dict[str, Any]]:
    primary_quality = measure_audio_quality(primary)
    if not external:
        return str(primary), {
            "selected": "primary",
            "reason": "No separate microphone supplied",
            "primary": primary_quality.to_dict(),
        }
    external_quality = measure_audio_quality(external)
    # Respect the creator's separate mic unless it is clearly broken/noisy/silent.
    use_external = external_quality.score >= max(18.0, primary_quality.score - 18.0)
    selected = str(external if use_external else primary)
    return selected, {
        "selected": "external" if use_external else "primary",
        "reason": (
            "Separate mic passed the automatic quality floor"
            if use_external
            else "Separate mic looked materially worse than camera audio; fell back to primary"
        ),
        "primary": primary_quality.to_dict(),
        "external": external_quality.to_dict(),
    }


def transcript_from_words(words: list[Word], language: str = "unknown") -> Transcript:
    if not words:
        return Transcript("", language, 0.0, [])
    ordered = sorted(words, key=lambda word: (word.start, word.end))
    segments: list[Segment] = []
    bucket: list[Word] = []
    for index, word in enumerate(ordered):
        bucket.append(word)
        nxt = ordered[index + 1] if index + 1 < len(ordered) else None
        gap = max(0.0, nxt.start - word.end) if nxt else 99.0
        terminal = word.text.rstrip().endswith((".", "!", "?"))
        if terminal or gap >= 0.55 or (bucket[-1].end - bucket[0].start) >= 8.0 or nxt is None:
            text = " ".join(item.text for item in bucket).strip()
            segments.append(Segment(bucket[0].start, bucket[-1].end, text, list(bucket)))
            bucket = []
    text = " ".join(segment.text for segment in segments).strip()
    duration = max((word.end for word in ordered), default=0.0)
    return Transcript(text, language, duration, segments)


def _intervals_changed(duration: float, intervals: list[KeepInterval]) -> bool:
    return not (
        len(intervals) == 1
        and abs(intervals[0].start) <= 0.001
        and abs(intervals[0].end - duration) <= 0.001
    )


def build_clean_master(
    source_path: str | Path,
    transcript: Transcript,
    output_path: str | Path,
    settings: Settings,
) -> tuple[Path, Transcript, list[dict[str, float]]]:
    """Apply global dead-air/filler cleanup before semantic clip selection.

    Auto mode intentionally cleans the authoritative precomp once, then selects
    clips from that cleaned timeline. That avoids independently tightening the
    same pause differently in each candidate and makes every downstream timestamp
    (selection, B-roll, captions) refer to one canonical edit timeline.
    """
    if transcript.duration <= 0 or not settings.auto_global_cleanup or not settings.smart_cut:
        return Path(source_path), transcript, [{"start": 0.0, "end": transcript.duration}]
    whole = ClipCandidate(
        id="auto_master",
        start=0.0,
        end=transcript.duration,
        score=0.0,
        title="Auto clean master",
        transcript=transcript.text,
    )
    intervals = build_keep_intervals(
        whole,
        transcript.words,
        remove_fillers=settings.remove_fillers,
        max_removed_ratio=settings.auto_cleanup_max_removed_ratio,
    )
    payload = [{"start": round(item.start, 4), "end": round(item.end, 4)} for item in intervals]
    if not _intervals_changed(transcript.duration, intervals):
        return Path(source_path), transcript, payload
    output = Path(output_path)
    prepare_compacted_clip(source_path, intervals, output)
    mapped = remap_words(transcript.words, intervals)
    return output, transcript_from_words(mapped, transcript.language), payload


def auto_edit_profile(candidate: ClipCandidate, settings: Settings) -> dict[str, Any]:
    """Choose edit density from the content instead of blindly maxing every effect."""
    pace = float(candidate.metrics.get("pace", 50.0))
    specificity = float(candidate.metrics.get("specificity", 50.0))
    hook = float(candidate.metrics.get("hook", 50.0))
    payoff = float(candidate.metrics.get("payoff", 50.0))
    duration = max(1.0, candidate.duration)

    base_cues = max(1, min(settings.broll_max_cues, math.ceil(duration / 11.0)))
    if specificity >= 70:
        base_cues = min(settings.broll_max_cues, base_cues + 1)
    elif specificity < 38:
        base_cues = max(1, base_cues - 1)

    # Very fast speech gets fewer punch-ins and cleaner captions; dense visual
    # motion on top of dense delivery is usually worse, not more engaging.
    punch_ins = settings.punch_ins and pace < 82
    caption_preset = "clean" if pace >= 82 else settings.caption_preset
    hook_overlay = settings.hook_overlay and (hook < 88 or payoff >= 65)
    return {
        "broll_max_cues": int(base_cues),
        "punch_ins": bool(punch_ins),
        "hook_overlay": bool(hook_overlay),
        "caption_preset": caption_preset,
        "pace": round(pace, 2),
        "specificity": round(specificity, 2),
    }
