from __future__ import annotations

import subprocess
from pathlib import Path

from ffmpeg_utils import DELIVERY, METADATA_SCRUB, video_encode_args

from .media import probe
from .models import SyncMap, Transcript
from .sync import ffmpeg_sync_filters


def replace_audio_with_synced_track(
    video_path: str | Path,
    audio_path: str | Path,
    sync: SyncMap,
    output_path: str | Path,
) -> Path:
    """Replace camera audio with a separately recorded microphone after drift/offset sync."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _, audio_filter = ffmpeg_sync_filters(sync)
    cmd = [
        "ffmpeg", "-y", "-v", "warning", "-i", str(video_path), "-i", str(audio_path),
        "-filter_complex", f"[1:a]{audio_filter}[aout]",
        "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", *METADATA_SCRUB, "-shortest", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def _primary_size(path: str | Path) -> tuple[int, int]:
    info = probe(path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream.get("width") or 1920), int(stream.get("height") or 1080)
    return 1920, 1080


def _cut_ranges(transcript: Transcript, min_seconds: float = 3.5, max_seconds: float = 8.0) -> list[tuple[float, float]]:
    duration = transcript.duration
    if duration <= 0:
        return []
    boundaries = [0.0]
    last = 0.0
    for seg in transcript.segments:
        candidate = min(duration, seg.end)
        if candidate - last >= min_seconds:
            boundaries.append(candidate)
            last = candidate
        if candidate - boundaries[-1] >= max_seconds:
            boundaries.append(candidate)
            last = candidate
    if duration - boundaries[-1] > 0.2:
        boundaries.append(duration)
    ranges = []
    for a, b in zip(boundaries, boundaries[1:]):
        if b - a > 0.15:
            ranges.append((a, b))
    return ranges


def build_multicam_master(
    primary_path: str | Path,
    secondary_cameras: list[tuple[str | Path, SyncMap]],
    transcript: Transcript,
    output_path: str | Path,
) -> Path:
    """Create an automatically cut multicam master on the primary timeline.

    Secondary camera timestamps are translated through their SyncMap. Cuts occur
    at speech-segment boundaries, which keeps edits away from the middle of words.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = _primary_size(primary_path)
    ranges = _cut_ranges(transcript)
    if not ranges or not secondary_cameras:
        return Path(primary_path)

    cameras: list[tuple[str | Path, SyncMap | None]] = [(primary_path, None)] + list(secondary_cameras)
    # Full primary is input 0 so its audio can run continuously beneath all camera cuts.
    cmd = ["ffmpeg", "-y", "-v", "warning", "-i", str(primary_path)]
    segment_meta: list[tuple[int, float, float]] = []
    input_index = 1
    for segment_index, (start, end) in enumerate(ranges):
        camera_index = segment_index % len(cameras)
        camera_path, sync = cameras[camera_index]
        source_start, source_end = start, end
        if sync is not None:
            source_start = (start - sync.intercept_seconds) / sync.rate
            source_end = (end - sync.intercept_seconds) / sync.rate
            if source_start < 0:
                camera_path, sync = cameras[0]
                source_start, source_end = start, end
        duration = max(0.1, source_end - source_start)
        cmd += ["-ss", f"{max(0.0, source_start):.3f}", "-t", f"{duration:.3f}", "-i", str(camera_path)]
        segment_meta.append((input_index, start, end))
        input_index += 1

    filters = []
    concat_inputs = []
    for n, (idx, start, end) in enumerate(segment_meta):
        label = f"v{n}"
        filters.append(
            f"[{idx}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,setpts=PTS-STARTPTS[{label}]"
        )
        concat_inputs.append(f"[{label}]")
    filters.append("".join(concat_inputs) + f"concat=n={len(segment_meta)}:v=1:a=0[vout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "0:a?"]
    cmd += video_encode_args(DELIVERY)
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-pix_fmt", "yuv420p"]
    cmd += METADATA_SCRUB
    cmd += ["-t", f"{transcript.duration:.3f}", str(out)]
    subprocess.run(cmd, check=True)
    return out
