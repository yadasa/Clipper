from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from ffmpeg_utils import DELIVERY, METADATA_SCRUB, audio_encode_args, video_encode_args

from .config import ASPECT_PRESETS
from .focus import crop_geometry, track_face_centers, write_sendcmd
from .media import probe
from .models import ClipCandidate, RenderedVariant, VisualCue, Word


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def write_ass(words: list[Word], candidate: ClipCandidate, path: str | Path, width: int, height: int) -> Path:
    local = [w for w in words if w.end >= candidate.start and w.start <= candidate.end]
    font_size = max(42, int(height * 0.042))
    margin_v = int(height * (0.17 if height > width else 0.09))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H001F1A16,&H66000000,-1,0,0,0,100,100,0,0,1,4,1,2,70,70,{margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = [header]
    for i in range(0, len(local), 4):
        chunk = local[i : i + 4]
        if not chunk:
            continue
        start = max(0.0, chunk[0].start - candidate.start)
        end = min(candidate.duration, chunk[-1].end - candidate.start + 0.08)
        text = _ass_escape(" ".join(w.text for w in chunk).upper())
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}\n")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(lines), encoding="utf-8")
    return out


def _escape_filter_path(path: str | Path) -> str:
    value = str(Path(path).resolve()).replace("\\", "/")
    value = value.replace(":", r"\:").replace("'", r"\'")
    return value


@lru_cache(maxsize=32)
def _source_dimensions(path: str) -> tuple[int, int]:
    try:
        info = probe(path)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
                if width > 0 and height > 0:
                    return width, height
    except Exception:
        pass
    return 0, 0


def _base_filter(
    source_path: str | Path,
    candidate: ClipCandidate,
    width: int,
    height: int,
    work_path: Path,
) -> str:
    """Build a subject-aware crop when possible, center-cropping as a safe fallback.

    Face analysis is low-resolution and cached per source clip, so rendering four
    aspect/layout variants does not repeat detector work. FFmpeg performs the
    actual full-resolution crop and scale natively.
    """
    source_w, source_h = _source_dimensions(str(Path(source_path).resolve()))
    if source_w <= 0 or source_h <= 0:
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"

    crop_w, crop_h = crop_geometry(source_w, source_h, width, height)
    center = f"crop@focus=w={crop_w}:h={crop_h}:x=(iw-ow)/2:y=(ih-oh)/2,scale={width}:{height},setsar=1"

    # Horizontal movement matters when the target format is narrower than the
    # source (the dominant 16:9 -> 9:16 creator workflow). If there is no useful
    # horizontal crop, skip detection entirely.
    if crop_w >= source_w - 2:
        return center

    try:
        points = track_face_centers(source_path, candidate.start, candidate.end)
        cmd_path = write_sendcmd(points, source_w, crop_w, work_path.with_suffix(".focus.cmd"))
        if cmd_path:
            escaped = _escape_filter_path(cmd_path)
            return f"sendcmd=f='{escaped}',{center}"
    except Exception:
        pass
    return center


def _mode_for(cue: VisualCue, ratio: str, index: int, requested: str) -> str:
    if requested in {"split", "pip", "interrupt"}:
        return requested
    allowed = cue.modes or ["split", "pip", "interrupt"]
    preference = {
        "9:16": ["split", "interrupt", "pip"],
        "4:5": ["split", "pip", "interrupt"],
        "1:1": ["pip", "split", "interrupt"],
        "16:9": ["pip", "split", "interrupt"],
    }[ratio]
    ordered = [x for x in preference if x in allowed]
    return ordered[index % len(ordered)] if ordered else "pip"


def render_clip(
    source_path: str | Path,
    candidate: ClipCandidate,
    transcript_words: list[Word],
    cues: list[VisualCue],
    output_path: str | Path,
    *,
    ratio: str = "9:16",
    layout_mode: str = "auto",
) -> RenderedVariant:
    if ratio not in ASPECT_PRESETS:
        raise ValueError(f"Unsupported aspect ratio: {ratio}")
    width, height = ASPECT_PRESETS[ratio]
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ass_path = write_ass(transcript_words, candidate, out.with_suffix(".ass"), width, height)

    usable = [cue for cue in cues if cue.asset_path and Path(cue.asset_path).is_file()]
    cmd = [
        "ffmpeg", "-y", "-v", "warning",
        "-ss", f"{candidate.start:.3f}", "-t", f"{candidate.duration:.3f}", "-i", str(source_path),
    ]
    for cue in usable:
        cmd += ["-loop", "1", "-framerate", "30", "-i", str(cue.asset_path)]

    base_filter = _base_filter(source_path, candidate, width, height, out)
    filters = [f"[0:v]{base_filter}[base0]"]
    previous = "base0"
    for i, cue in enumerate(usable, 1):
        mode = _mode_for(cue, ratio, i - 1, layout_mode)
        visual = f"vis{i}"
        nxt = f"base{i}"
        start = max(0.0, cue.start)
        end = min(candidate.duration, max(start + 0.3, cue.end))
        enable = f"enable='between(t,{start:.3f},{end:.3f})'"
        if mode == "interrupt":
            filters.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[{visual}]")
            filters.append(f"[{previous}][{visual}]overlay=0:0:{enable}[{nxt}]")
        elif mode == "split":
            if height >= width:
                panel_h = int(height * 0.43)
                filters.append(f"[{i}:v]scale={width}:{panel_h}:force_original_aspect_ratio=increase,crop={width}:{panel_h},setsar=1[{visual}]")
                filters.append(f"[{previous}][{visual}]overlay=0:0:{enable}[{nxt}]")
            else:
                panel_w = int(width * 0.42)
                filters.append(f"[{i}:v]scale={panel_w}:{height}:force_original_aspect_ratio=increase,crop={panel_w}:{height},setsar=1[{visual}]")
                filters.append(f"[{previous}][{visual}]overlay=W-w:0:{enable}[{nxt}]")
        else:
            pip_w = int(width * (0.42 if height >= width else 0.34))
            filters.append(f"[{i}:v]scale={pip_w}:-2:force_original_aspect_ratio=decrease,setsar=1[{visual}]")
            margin = max(24, int(min(width, height) * 0.025))
            filters.append(f"[{previous}][{visual}]overlay=W-w-{margin}:{margin}:{enable}[{nxt}]")
        previous = nxt

    ass = _escape_filter_path(ass_path)
    filters.append(f"[{previous}]subtitles='{ass}'[vout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "0:a?"]
    cmd += video_encode_args(DELIVERY)
    cmd += audio_encode_args()
    cmd += ["-b:a", "192k", "-movflags", "+faststart", "-pix_fmt", "yuv420p"]
    cmd += METADATA_SCRUB
    cmd += ["-t", f"{candidate.duration:.3f}", str(out)]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to render clips") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg render failed for {candidate.id} {ratio}: {exc}") from exc
    return RenderedVariant(candidate.id, ratio, str(out), width, height)


def render_variants(
    source_path: str | Path,
    candidate: ClipCandidate,
    transcript_words: list[Word],
    cues: list[VisualCue],
    output_root: str | Path,
    ratios: list[str],
    *,
    layout_modes: list[str] | None = None,
) -> list[RenderedVariant]:
    """Render aspect-ratio variants and optional alternate edit compositions.

    Independent FFmpeg jobs can run concurrently. Keep the default conservative
    (2) so CPU desktops stay responsive and consumer NVENC session limits are not
    hammered; set RENDER_WORKERS=1 for strictly serial output.
    """
    modes = layout_modes or ["auto"]
    specs: list[tuple[str, str, Path]] = []
    for ratio in ratios:
        ratio_dir = ratio.replace(":", "x")
        for mode in modes:
            suffix = "" if mode == "auto" else f"-{mode}"
            path = Path(output_root) / candidate.id / ratio_dir / f"{candidate.id}-{ratio_dir}{suffix}.mp4"
            specs.append((ratio, mode, path))

    workers = max(1, int(os.getenv("RENDER_WORKERS", "2")))
    workers = min(workers, len(specs)) if specs else 1

    def run(spec: tuple[str, str, Path]) -> RenderedVariant:
        ratio, mode, path = spec
        return render_clip(source_path, candidate, transcript_words, cues, path, ratio=ratio, layout_mode=mode)

    if workers == 1:
        return [run(spec) for spec in specs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, specs))
