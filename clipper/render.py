from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from ffmpeg_utils import DELIVERY, METADATA_SCRUB, audio_encode_args, video_encode_args

from .audio import has_audio, music_input_args, music_mix_filters
from .brand import BrandKit
from .captions import write_captions
from .config import ASPECT_PRESETS
from .focus import crop_geometry, track_face_centers, write_sendcmd
from .media import probe
from .models import ClipCandidate, RenderedVariant, VisualCue, Word


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
    source_w, source_h = _source_dimensions(str(Path(source_path).resolve()))
    if source_w <= 0 or source_h <= 0:
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"

    crop_w, crop_h = crop_geometry(source_w, source_h, width, height)
    center = f"crop@focus=w={crop_w}:h={crop_h}:x=(iw-ow)/2:y=(ih-oh)/2,scale={width}:{height},setsar=1"
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


def _logo_position(brand: BrandKit, margin: int) -> str:
    return {
        "top-left": f"{margin}:{margin}",
        "top-right": f"W-w-{margin}:{margin}",
        "bottom-left": f"{margin}:H-h-{margin}",
        "bottom-right": f"W-w-{margin}:H-h-{margin}",
    }.get(brand.logo_position, f"W-w-{margin}:{margin}")


def render_clip(
    source_path: str | Path,
    candidate: ClipCandidate,
    transcript_words: list[Word],
    cues: list[VisualCue],
    output_path: str | Path,
    *,
    ratio: str = "9:16",
    layout_mode: str = "auto",
    brand: BrandKit | None = None,
    caption_preset: str | None = None,
    hook_text: str | None = None,
    music_path: str | None = None,
) -> RenderedVariant:
    if ratio not in ASPECT_PRESETS:
        raise ValueError(f"Unsupported aspect ratio: {ratio}")
    brand = brand or BrandKit()
    width, height = ASPECT_PRESETS[ratio]
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ass_path = write_captions(
        transcript_words,
        candidate,
        out.with_suffix(".ass"),
        width,
        height,
        brand,
        preset=caption_preset,
        hook_text=hook_text,
    )

    usable = [cue for cue in cues if cue.asset_path and Path(cue.asset_path).is_file()]
    logo_path = Path(brand.logo_path).expanduser() if brand.logo_path else None
    if logo_path and not logo_path.is_file():
        logo_path = None
    music = Path(music_path).expanduser() if music_path else None
    if music and not music.is_file():
        music = None

    cmd = [
        "ffmpeg", "-y", "-v", "warning",
        "-ss", f"{candidate.start:.3f}", "-t", f"{candidate.duration:.3f}", "-i", str(source_path),
    ]
    input_index = 1
    visual_inputs: list[int] = []
    for cue in usable:
        cmd += ["-loop", "1", "-framerate", "30", "-i", str(cue.asset_path)]
        visual_inputs.append(input_index)
        input_index += 1
    logo_index = None
    if logo_path:
        cmd += ["-loop", "1", "-framerate", "30", "-i", str(logo_path)]
        logo_index = input_index
        input_index += 1
    music_index = None
    if music:
        cmd += music_input_args(music)
        music_index = input_index
        input_index += 1

    base_filter = _base_filter(source_path, candidate, width, height, out)
    filters = [f"[0:v]{base_filter}[base0]"]
    previous = "base0"
    for position, (cue, source_index) in enumerate(zip(usable, visual_inputs), 1):
        mode = _mode_for(cue, ratio, position - 1, layout_mode)
        visual = f"vis{position}"
        nxt = f"base{position}"
        start = max(0.0, cue.start)
        end = min(candidate.duration, max(start + 0.3, cue.end))
        enable = f"enable='between(t,{start:.3f},{end:.3f})'"
        if mode == "interrupt":
            filters.append(f"[{source_index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[{visual}]")
            filters.append(f"[{previous}][{visual}]overlay=0:0:{enable}[{nxt}]")
        elif mode == "split":
            if height >= width:
                panel_h = int(height * 0.43)
                filters.append(f"[{source_index}:v]scale={width}:{panel_h}:force_original_aspect_ratio=increase,crop={width}:{panel_h},setsar=1[{visual}]")
                filters.append(f"[{previous}][{visual}]overlay=0:0:{enable}[{nxt}]")
            else:
                panel_w = int(width * 0.42)
                filters.append(f"[{source_index}:v]scale={panel_w}:{height}:force_original_aspect_ratio=increase,crop={panel_w}:{height},setsar=1[{visual}]")
                filters.append(f"[{previous}][{visual}]overlay=W-w:0:{enable}[{nxt}]")
        else:
            pip_w = int(width * (0.42 if height >= width else 0.34))
            filters.append(f"[{source_index}:v]scale={pip_w}:-2:force_original_aspect_ratio=decrease,setsar=1[{visual}]")
            margin = max(24, int(min(width, height) * 0.025))
            filters.append(f"[{previous}][{visual}]overlay=W-w-{margin}:{margin}:{enable}[{nxt}]")
        previous = nxt

    if logo_index is not None:
        logo_label = "brandlogo"
        nxt = "brandbase"
        logo_w = max(90, int(width * (0.13 if height >= width else 0.10)))
        margin = max(24, int(min(width, height) * 0.025))
        filters.append(f"[{logo_index}:v]scale={logo_w}:-2,format=rgba[{logo_label}]")
        filters.append(f"[{previous}][{logo_label}]overlay={_logo_position(brand, margin)}:format=auto[{nxt}]")
        previous = nxt

    ass = _escape_filter_path(ass_path)
    filters.append(f"[{previous}]subtitles='{ass}'[vout]")

    source_has_audio = has_audio(source_path)
    audio_map: list[str] = []
    audio_codec: list[str] = []
    if music_index is not None and source_has_audio:
        filters.extend(music_mix_filters(music_index))
        audio_map = ["-map", "[aout]"]
        audio_codec = ["-c:a", "aac", "-b:a", "192k"]
    elif music_index is not None:
        filters.append(f"[{music_index}:a]aresample=48000,volume=0.22,loudnorm=I=-14:TP=-2.0:LRA=11[aout]")
        audio_map = ["-map", "[aout]"]
        audio_codec = ["-c:a", "aac", "-b:a", "192k"]
    elif source_has_audio:
        audio_map = ["-map", "0:a:0"]
        audio_codec = [*audio_encode_args(), "-b:a", "192k"]

    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", *audio_map]
    cmd += video_encode_args(DELIVERY)
    cmd += audio_codec
    cmd += ["-movflags", "+faststart", "-pix_fmt", "yuv420p"]
    cmd += METADATA_SCRUB
    cmd += ["-t", f"{candidate.duration:.3f}", str(out)]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to render clips") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg render failed for {candidate.id} {ratio}: {exc}") from exc
    return RenderedVariant(candidate.id, ratio, str(out), width, height, layout_mode=layout_mode)


def render_variants(
    source_path: str | Path,
    candidate: ClipCandidate,
    transcript_words: list[Word],
    cues: list[VisualCue],
    output_root: str | Path,
    ratios: list[str],
    *,
    layout_modes: list[str] | None = None,
    brand: BrandKit | None = None,
    caption_preset: str | None = None,
    hook_text: str | None = None,
    music_path: str | None = None,
) -> list[RenderedVariant]:
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
        return render_clip(
            source_path,
            candidate,
            transcript_words,
            cues,
            path,
            ratio=ratio,
            layout_mode=mode,
            brand=brand,
            caption_preset=caption_preset,
            hook_text=hook_text,
            music_path=music_path,
        )

    if workers == 1:
        return [run(spec) for spec in specs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, specs))
