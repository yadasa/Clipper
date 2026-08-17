from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ffmpeg_utils import QUALITY_FAST, METADATA_SCRUB, audio_encode_args, video_encode_args

from .media import duration as media_duration, probe
from .models import Word

EMPHASIS_WORDS = {
    "never", "always", "best", "worst", "biggest", "secret", "truth", "important",
    "crazy", "actually", "exactly", "money", "free", "mistake", "problem", "fix",
    "first", "finally", "stop", "listen", "watch", "remember", "why", "how",
}


@dataclass(slots=True, frozen=True)
class PunchIn:
    start: float
    end: float
    scale: float = 1.08


def plan_punch_ins(words: list[Word], clip_duration: float, *, min_gap: float = 4.5) -> list[PunchIn]:
    """Plan sparse emphasis zooms from transcript language and sentence rhythm."""
    if clip_duration <= 0:
        return []
    events: list[PunchIn] = []
    last = -999.0
    for index, word in enumerate(words):
        token = re.sub(r"[^a-z0-9']+", "", word.text.lower())
        previous_end = words[index - 1].end if index > 0 else 0.0
        pause_before = max(0.0, word.start - previous_end)
        strong = token in EMPHASIS_WORDS or pause_before >= 0.55
        if not strong or word.start - last < min_gap:
            continue
        start = max(0.0, word.start - 0.18)
        end = min(clip_duration, max(word.end + 1.15, start + 1.3))
        if end - start >= 0.8:
            scale = 1.10 if token in {"never", "secret", "biggest", "worst", "best", "truth"} else 1.07
            events.append(PunchIn(start, end, scale))
            last = start
        if len(events) >= max(2, int(clip_duration // 8)):
            break
    return events


def _dimensions(path: str | Path) -> tuple[int, int]:
    info = probe(path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream.get("width") or 0), int(stream.get("height") or 0)
    return 0, 0


def _segments(duration: float, events: list[PunchIn]) -> list[tuple[float, float, float]]:
    boundaries = {0.0, max(0.0, duration)}
    for event in events:
        boundaries.add(max(0.0, min(duration, event.start)))
        boundaries.add(max(0.0, min(duration, event.end)))
    points = sorted(boundaries)
    result = []
    for start, end in zip(points, points[1:]):
        if end - start < 0.02:
            continue
        mid = (start + end) / 2
        scale = 1.0
        for event in events:
            if event.start <= mid < event.end:
                scale = max(scale, event.scale)
        result.append((start, end, scale))
    return result


def apply_punch_ins(source_path: str | Path, events: list[PunchIn], output_path: str | Path) -> Path:
    if not events:
        return Path(source_path)
    width, height = _dimensions(source_path)
    if width <= 0 or height <= 0:
        return Path(source_path)
    duration = media_duration(source_path)
    segments = _segments(duration, events)
    if not segments:
        return Path(source_path)

    filters: list[str] = []
    parts: list[str] = []
    for index, (start, end, scale) in enumerate(segments):
        video = f"[0:v]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS"
        if scale > 1.001:
            scaled_w = int(round(width * scale / 2) * 2)
            scaled_h = int(round(height * scale / 2) * 2)
            video += f",scale={scaled_w}:{scaled_h},crop={width}:{height}:(iw-ow)/2:(ih-oh)/2"
        video += f"[v{index}]"
        audio = f"[0:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[a{index}]"
        filters.extend([video, audio])
        parts.append(f"[v{index}][a{index}]")
    filters.append("".join(parts) + f"concat=n={len(segments)}:v=1:a=1[vout][aout]")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "warning", "-i", str(source_path),
        "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
        *video_encode_args(QUALITY_FAST), *audio_encode_args(), "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", *METADATA_SCRUB, str(out),
    ]
    subprocess.run(cmd, check=True)
    return out
