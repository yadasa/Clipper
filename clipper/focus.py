from __future__ import annotations

import json
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

_cache_lock = threading.Lock()
_CACHE_LIMIT = 64
_cache: OrderedDict[tuple[str, int, int, float, float, float, int], list[tuple[float, float]]] = OrderedDict()


def _video_size(source_path: str | Path) -> tuple[int, int]:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "json", str(source_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        stream = (json.loads(result.stdout).get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except Exception:
        return 0, 0


def _read_exact(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _cache_key(
    source_path: str | Path,
    start: float,
    end: float,
    sample_hz: float,
    analysis_width: int,
) -> tuple[str, int, int, float, float, float, int] | None:
    path = Path(source_path).expanduser().resolve()
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        str(path),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        round(float(start), 3),
        round(float(end), 3),
        round(float(sample_hz), 3),
        int(analysis_width),
    )


def _cache_get(key):
    if key is None:
        return None
    with _cache_lock:
        cached = _cache.get(key)
        if cached is None:
            return None
        _cache.move_to_end(key)
        return list(cached)


def _cache_put(key, points: list[tuple[float, float]]) -> None:
    if key is None:
        return
    with _cache_lock:
        _cache[key] = list(points)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)


def track_face_centers(
    source_path: str | Path,
    start: float,
    end: float,
    *,
    sample_hz: float = 5.0,
    analysis_width: int = 480,
) -> list[tuple[float, float]]:
    """Return (clip-local seconds, normalized x) for the dominant face.

    FFmpeg seeks once, samples only a few frames per second, and downscales before
    pixels cross into Python. This avoids repeated random seeks and avoids a
    full-resolution/full-frame-rate OpenCV decode. A sticky nearest-target rule
    plus exponential smoothing keeps background faces from making the crop whip.
    Failure is intentionally soft so callers can fall back to a center crop.
    """
    key = _cache_key(source_path, start, end, sample_hz, analysis_width)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        import mediapipe as mp
        import numpy as np
    except Exception:
        return []

    source_w, source_h = _video_size(source_path)
    if source_w <= 0 or source_h <= 0 or end <= start:
        return []

    small_w = min(max(160, int(analysis_width)), source_w)
    if small_w % 2:
        small_w -= 1
    small_h = max(2, int(round(source_h * small_w / source_w)))
    if small_h % 2:
        small_h += 1
    hz = max(0.5, float(sample_hz))
    duration = max(0.1, float(end) - float(start))
    frame_bytes = small_w * small_h * 3

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, float(start)):.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(source_path),
        "-an", "-sn", "-dn",
        "-vf", f"fps={hz:.6f},scale={small_w}:{small_h}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=frame_bytes * 2,
        )
    except Exception:
        return []
    if proc.stdout is None:
        proc.kill()
        return []

    detector = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.45)
    points: list[tuple[float, float]] = []
    previous = 0.5
    smoothed = 0.5
    sample_index = 0
    try:
        while True:
            raw = _read_exact(proc.stdout, frame_bytes)
            if len(raw) != frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((small_h, small_w, 3))
            result = detector.process(frame)
            candidates = []
            for detection in result.detections or []:
                box = detection.location_data.relative_bounding_box
                cx = float(box.xmin + box.width / 2)
                area = max(0.0, float(box.width * box.height))
                if -0.15 <= cx <= 1.15:
                    # Prefer continuity; foreground area breaks otherwise-close ties.
                    score = abs(cx - previous) - min(0.25, area)
                    candidates.append((score, max(0.0, min(1.0, cx))))
            target = min(candidates, key=lambda item: item[0])[1] if candidates else previous
            if abs(target - previous) > 0.32 and candidates:
                target = previous + (0.18 if target > previous else -0.18)
            previous = target
            alpha = 0.30
            smoothed = smoothed * (1.0 - alpha) + target * alpha
            points.append((sample_index / hz, max(0.0, min(1.0, smoothed))))
            sample_index += 1
    finally:
        detector.close()
        try:
            proc.stdout.close()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    compact: list[tuple[float, float]] = []
    for t, x in points:
        if not compact or abs(x - compact[-1][1]) >= 0.008 or t - compact[-1][0] >= 1.0:
            compact.append((t, x))
    _cache_put(key, compact)
    return compact


def crop_geometry(source_width: int, source_height: int, target_width: int, target_height: int) -> tuple[int, int]:
    target_ratio = target_width / target_height
    source_ratio = source_width / source_height
    if source_ratio >= target_ratio:
        crop_h = source_height
        crop_w = int(round(crop_h * target_ratio))
    else:
        crop_w = source_width
        crop_h = int(round(crop_w / target_ratio))
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2
    return max(2, crop_w), max(2, crop_h)


def write_sendcmd(
    points: list[tuple[float, float]],
    source_width: int,
    crop_width: int,
    path: str | Path,
) -> Path | None:
    if not points or crop_width >= source_width:
        return None
    max_x = max(0, source_width - crop_width)
    lines = []
    previous = None
    for t, normalized_x in points:
        x = int(round(normalized_x * source_width - crop_width / 2))
        x = max(0, min(max_x, x))
        if previous is None or abs(x - previous) >= 2:
            lines.append(f"{max(0.0, t):.4f} crop@focus x {x};")
            previous = x
    if not lines:
        return None
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
