from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ffmpeg_utils import QUALITY_FAST, METADATA_SCRUB, audio_encode_args, video_encode_args

from .models import ClipCandidate, Word

FILLERS = {
    "um", "uh", "erm", "er", "hmm", "mmm", "ah", "eh",
    "basically", "literally", "actually", "like",
}


@dataclass(slots=True, frozen=True)
class KeepInterval:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _merge_ranges(ranges: list[tuple[float, float]], padding: float = 0.025) -> list[tuple[float, float]]:
    clean = sorted((max(0.0, a), max(0.0, b)) for a, b in ranges if b > a)
    if not clean:
        return []
    merged = [list(clean[0])]
    for start, end in clean[1:]:
        if start <= merged[-1][1] + padding:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _removals_for_words(
    candidate: ClipCandidate,
    words: list[Word],
    *,
    max_silence: float,
    retained_silence: float,
    remove_fillers: bool,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    local = sorted(
        [w for w in words if w.end > candidate.start and w.start < candidate.end],
        key=lambda w: w.start,
    )
    silence: list[tuple[float, float]] = []
    fillers: list[tuple[float, float]] = []

    previous_end = candidate.start
    for index, word in enumerate(local):
        gap = max(0.0, word.start - previous_end)
        if gap > max_silence:
            trim = max(0.0, (gap - retained_silence) / 2)
            silence.append((previous_end + retained_silence / 2, word.start - retained_silence / 2))

        token = word.text.strip().lower().strip(".,!?;:\"'()[]{}")
        if remove_fillers and token in FILLERS and word.end - word.start <= 1.2:
            left = max(candidate.start, local[index - 1].end if index > 0 else candidate.start)
            right = min(candidate.end, local[index + 1].start if index + 1 < len(local) else candidate.end)
            left_pause = max(0.0, word.start - left)
            right_pause = max(0.0, right - word.end)
            # Only cut a filler when there is enough room to hide the edit.
            if left_pause + right_pause >= 0.24 and max(left_pause, right_pause) >= 0.12:
                fillers.append((max(left, word.start - min(0.06, left_pause)), min(right, word.end + min(0.06, right_pause))))
        previous_end = max(previous_end, word.end)

    tail_gap = max(0.0, candidate.end - previous_end)
    if tail_gap > max_silence:
        silence.append((previous_end + retained_silence / 2, candidate.end - retained_silence / 2))
    return silence, fillers


def build_keep_intervals(
    candidate: ClipCandidate,
    words: list[Word],
    *,
    max_silence: float = 0.70,
    retained_silence: float = 0.14,
    remove_fillers: bool = True,
    max_removed_ratio: float = 0.35,
) -> list[KeepInterval]:
    """Create a conservative edit decision list for talking-head cleanup."""
    if candidate.duration <= 0:
        return []
    silence, fillers = _removals_for_words(
        candidate,
        words,
        max_silence=max_silence,
        retained_silence=retained_silence,
        remove_fillers=remove_fillers,
    )
    removals = _merge_ranges(silence + fillers)
    removed = sum(end - start for start, end in removals)
    if removed / candidate.duration > max_removed_ratio:
        removals = _merge_ranges(silence)

    cursor = candidate.start
    keep: list[KeepInterval] = []
    for start, end in removals:
        start = max(candidate.start, min(candidate.end, start))
        end = max(candidate.start, min(candidate.end, end))
        if start - cursor >= 0.10:
            keep.append(KeepInterval(cursor, start))
        cursor = max(cursor, end)
    if candidate.end - cursor >= 0.10:
        keep.append(KeepInterval(cursor, candidate.end))
    if not keep:
        return [KeepInterval(candidate.start, candidate.end)]

    # Avoid micro-segments: merge anything under 180 ms into the previous segment
    # by restoring the cut between them.
    compact: list[KeepInterval] = []
    for interval in keep:
        if compact and interval.duration < 0.18:
            compact[-1] = KeepInterval(compact[-1].start, interval.end)
        else:
            compact.append(interval)
    return compact


def compact_duration(intervals: list[KeepInterval]) -> float:
    return sum(interval.duration for interval in intervals)


def remap_words(words: list[Word], intervals: list[KeepInterval]) -> list[Word]:
    result: list[Word] = []
    offset = 0.0
    for interval in intervals:
        for word in words:
            if word.end <= interval.start or word.start >= interval.end:
                continue
            start = offset + max(0.0, word.start - interval.start)
            end = offset + min(interval.duration, word.end - interval.start)
            if end > start:
                result.append(Word(word.text, start, end))
        offset += interval.duration
    # A word can only straddle one removed boundary in pathological transcripts;
    # dedupe by rounded timing/text to keep caption generation stable.
    deduped: list[Word] = []
    seen = set()
    for word in result:
        key = (word.text, round(word.start, 3), round(word.end, 3))
        if key not in seen:
            seen.add(key)
            deduped.append(word)
    return deduped


def prepare_compacted_clip(
    source_path: str | Path,
    intervals: list[KeepInterval],
    output_path: str | Path,
) -> Path:
    """Render the EDL into a compact intermediate clip.

    Uses one FFmpeg process and concat filter rather than invoking FFmpeg once per
    jump cut. This is dramatically faster for creator footage with many pauses.
    """
    if not intervals:
        raise ValueError("At least one keep interval is required")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    filters: list[str] = []
    concat_parts: list[str] = []
    for index, interval in enumerate(intervals):
        filters.append(
            f"[0:v]trim=start={interval.start:.6f}:end={interval.end:.6f},setpts=PTS-STARTPTS[v{index}]"
        )
        filters.append(
            f"[0:a]atrim=start={interval.start:.6f}:end={interval.end:.6f},asetpts=PTS-STARTPTS[a{index}]"
        )
        concat_parts.append(f"[v{index}][a{index}]")
    filters.append("".join(concat_parts) + f"concat=n={len(intervals)}:v=1:a=1[vout][aout]")
    cmd = [
        "ffmpeg", "-y", "-v", "warning", "-i", str(source_path),
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        *video_encode_args(QUALITY_FAST),
        *audio_encode_args(),
        "-b:a", "192k", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        *METADATA_SCRUB,
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required for smart cuts") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Smart-cut FFmpeg render failed: {exc}") from exc
    return out
