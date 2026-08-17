from __future__ import annotations

from pathlib import Path

from .media import probe
from .models import Word


def has_audio(path: str | Path) -> bool:
    try:
        return any(stream.get("codec_type") == "audio" for stream in probe(path).get("streams", []))
    except Exception:
        return False


def music_input_args(path: str | Path | None) -> list[str]:
    if not path:
        return []
    music = Path(path).expanduser()
    if not music.is_file():
        return []
    return ["-stream_loop", "-1", "-i", str(music)]


def speech_intervals(
    words: list[Word],
    *,
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    pad_before: float = 0.10,
    pad_after: float = 0.18,
    merge_gap: float = 0.16,
) -> list[tuple[float, float]]:
    """Build merged clip-local speech windows from word timestamps.

    Transcript-driven ducking is deterministic, portable across FFmpeg builds,
    and does not depend on the optional ``sidechaincompress`` filter.
    """
    limit = float("inf") if clip_duration is None else max(0.0, float(clip_duration))
    raw: list[tuple[float, float]] = []
    for word in words:
        start = max(0.0, float(word.start) - clip_start - pad_before)
        end = min(limit, float(word.end) - clip_start + pad_after)
        if end > start:
            raw.append((start, end))
    if not raw:
        return []
    raw.sort()
    merged: list[list[float]] = [[raw[0][0], raw[0][1]]]
    for start, end in raw[1:]:
        if start <= merged[-1][1] + merge_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(start, 4), round(end, 4)) for start, end in merged]


def _duck_expression(
    intervals: list[tuple[float, float]],
    normal_gain: float,
    duck_gain: float,
) -> str:
    normal = max(0.0, min(1.0, float(normal_gain)))
    duck = max(0.0, min(normal, float(duck_gain)))
    if not intervals:
        # With source speech but no trustworthy word timings, the safe fallback
        # is to keep the bed conservatively low rather than risk masking dialogue.
        return f"{duck:.4f}"
    condition = "+".join(f"between(t,{start:.4f},{end:.4f})" for start, end in intervals)
    return f"if({condition},{duck:.4f},{normal:.4f})"


def music_mix_filters(
    music_input_index: int,
    *,
    speech_words: list[Word] | None = None,
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    speech_label: str = "0:a",
    output_label: str = "aout",
    music_gain: float = 0.22,
    duck_gain: float = 0.065,
) -> list[str]:
    """Mix a transcript-aware music bed beneath speech.

    Music sits around ``music_gain`` between spoken phrases and drops to
    ``duck_gain`` during merged word-timestamp windows. If timings are unavailable,
    it stays at the conservative duck level. The resulting mix is then loudness-
    normalized for social delivery and does not require sidechain compression.
    """
    intervals = speech_intervals(
        list(speech_words or []),
        clip_start=clip_start,
        clip_duration=clip_duration,
    )
    expression = _duck_expression(intervals, music_gain, duck_gain)
    return [
        f"[{speech_label}]aresample=48000[speech]",
        f"[{music_input_index}:a]aresample=48000,volume='{expression}':eval=frame[ducked]",
        f"[speech][ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,loudnorm=I=-14:TP=-2.0:LRA=11[{output_label}]",
    ]
