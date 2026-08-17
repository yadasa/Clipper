from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ffmpeg_utils import DELIVERY, METADATA_SCRUB, video_encode_args

from .media import duration as media_duration, probe
from .models import SyncMap, Transcript
from .sync import ffmpeg_sync_filters


def _temp_media_path(output_path: str | Path) -> tuple[Path, Path]:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_name(f"{out.stem}.part{out.suffix or '.mp4'}")
    temp.unlink(missing_ok=True)
    return out, temp


def _finalize_ffmpeg(cmd: list[str], temp: Path, out: Path, label: str) -> Path:
    try:
        subprocess.run(cmd, check=True)
        if not temp.is_file() or temp.stat().st_size <= 0:
            raise RuntimeError(f"{label} FFmpeg produced no output")
        os.replace(temp, out)
        return out
    except FileNotFoundError as exc:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg is required for {label.lower()}") from exc
    except subprocess.CalledProcessError as exc:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"{label} FFmpeg render failed: {exc}") from exc
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def replace_audio_with_synced_track(
    video_path: str | Path,
    audio_path: str | Path,
    sync: SyncMap,
    output_path: str | Path,
) -> Path:
    """Replace camera audio with a separately recorded microphone after drift/offset sync."""
    out, temp = _temp_media_path(output_path)
    _, audio_filter = ffmpeg_sync_filters(sync)
    cmd = [
        "ffmpeg", "-y", "-v", "warning", "-i", str(video_path), "-i", str(audio_path),
        "-filter_complex", f"[1:a]{audio_filter}[aout]",
        "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", *METADATA_SCRUB, "-shortest", str(temp),
    ]
    return _finalize_ffmpeg(cmd, temp, out, "Synced-audio")


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
    for segment in transcript.segments:
        candidate = min(duration, segment.end)
        if candidate - last >= min_seconds:
            boundaries.append(candidate)
            last = candidate
        elif candidate - boundaries[-1] >= max_seconds:
            boundaries.append(candidate)
            last = candidate
    if duration - boundaries[-1] > 0.2:
        boundaries.append(duration)
    return [
        (start, end)
        for start, end in zip(boundaries, boundaries[1:])
        if end - start > 0.15
    ]


def _mapped_secondary_range(
    start: float,
    end: float,
    sync: SyncMap,
    camera_duration: float,
) -> tuple[float, float] | None:
    if sync.rate <= 0:
        return None
    source_start = (start - sync.intercept_seconds) / sync.rate
    source_end = (end - sync.intercept_seconds) / sync.rate
    if source_start < 0 or source_end <= source_start:
        return None
    if camera_duration > 0 and source_end > camera_duration + 0.05:
        return None
    return source_start, source_end


def build_multicam_master(
    primary_path: str | Path,
    secondary_cameras: list[tuple[str | Path, SyncMap]],
    transcript: Transcript,
    output_path: str | Path,
) -> Path:
    """Create an automatically cut multicam master on the primary timeline.

    Secondary camera timestamps are translated through their SyncMap. Cuts occur
    at speech-segment boundaries, which keeps edits away from the middle of words.
    A secondary shot is used only when that camera actually covers the requested
    mapped time range; otherwise the director safely falls back to the primary.
    """
    width, height = _primary_size(primary_path)
    ranges = _cut_ranges(transcript)
    if not ranges or not secondary_cameras:
        return Path(primary_path)

    cameras: list[tuple[str | Path, SyncMap | None, float]] = [
        (primary_path, None, media_duration(primary_path))
    ]
    for camera_path, sync in secondary_cameras:
        cameras.append((camera_path, sync, media_duration(camera_path)))

    # Full primary is input 0 so its audio can run continuously beneath all cuts.
    cmd = ["ffmpeg", "-y", "-v", "warning", "-i", str(primary_path)]
    segment_meta: list[tuple[int, float, float]] = []
    input_index = 1
    for segment_index, (start, end) in enumerate(ranges):
        camera_index = segment_index % len(cameras)
        camera_path, sync, camera_length = cameras[camera_index]
        source_start, source_end = start, end
        if sync is not None:
            mapped = _mapped_secondary_range(start, end, sync, camera_length)
            if mapped is None:
                camera_path = primary_path
                source_start, source_end = start, end
            else:
                source_start, source_end = mapped
        duration = max(0.1, source_end - source_start)
        cmd += [
            "-ss", f"{max(0.0, source_start):.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(camera_path),
        ]
        segment_meta.append((input_index, start, end))
        input_index += 1

    filters = []
    concat_inputs = []
    for index, (input_id, _start, _end) in enumerate(segment_meta):
        label = f"v{index}"
        filters.append(
            f"[{input_id}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,setpts=PTS-STARTPTS[{label}]"
        )
        concat_inputs.append(f"[{label}]")
    filters.append("".join(concat_inputs) + f"concat=n={len(segment_meta)}:v=1:a=0[vout]")

    out, temp = _temp_media_path(output_path)
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "0:a?"]
    cmd += video_encode_args(DELIVERY)
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-pix_fmt", "yuv420p"]
    cmd += METADATA_SCRUB
    cmd += ["-t", f"{transcript.duration:.3f}", str(temp)]
    return _finalize_ffmpeg(cmd, temp, out, "Multicam")
