from __future__ import annotations

import threading
from pathlib import Path

_cache_lock = threading.Lock()
_cache: dict[tuple[str, float, float], list[tuple[float, float]]] = {}


def track_face_centers(
    source_path: str | Path,
    start: float,
    end: float,
    *,
    sample_hz: float = 5.0,
    analysis_width: int = 480,
) -> list[tuple[float, float]]:
    """Return (clip-local seconds, normalized x) for the dominant face.

    Frames are sampled instead of decoded at full frame rate. A sticky nearest-
    target rule plus exponential smoothing prevents a second/background face
    from making the crop whip across the shot. Failure is intentionally soft:
    callers can use a center crop.
    """
    key = (str(Path(source_path).resolve()), round(float(start), 3), round(float(end), 3))
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return list(cached)

    try:
        import cv2
        import mediapipe as mp
    except Exception:
        return []

    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        return []
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        cap.release()
        return []
    stride = max(1, int(round(fps / max(0.5, sample_hz))))
    start_frame = max(0, int(start * fps))
    end_frame = max(start_frame + 1, int(end * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    detector = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.45)
    points: list[tuple[float, float]] = []
    previous = 0.5
    smoothed = 0.5
    frame_index = start_frame
    try:
        while frame_index < end_frame:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            if w > analysis_width:
                new_h = max(2, int(h * analysis_width / w))
                frame = cv2.resize(frame, (analysis_width, new_h))
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = detector.process(rgb)
            candidates = []
            for detection in result.detections or []:
                box = detection.location_data.relative_bounding_box
                cx = float(box.xmin + box.width / 2)
                area = max(0.0, float(box.width * box.height))
                if -0.15 <= cx <= 1.15:
                    # Prefer the current subject; area breaks ties toward foreground faces.
                    score = abs(cx - previous) - min(0.25, area)
                    candidates.append((score, max(0.0, min(1.0, cx))))
            target = min(candidates, key=lambda item: item[0])[1] if candidates else previous
            # Ignore single huge jumps; real movement will recur in subsequent samples.
            if abs(target - previous) > 0.32 and candidates:
                target = previous + (0.18 if target > previous else -0.18)
            previous = target
            alpha = 0.30
            smoothed = smoothed * (1.0 - alpha) + target * alpha
            points.append(((frame_index / fps) - start, max(0.0, min(1.0, smoothed))))
            frame_index += stride
    finally:
        detector.close()
        cap.release()

    # Dedupe tiny changes so FFmpeg's sendcmd file stays small.
    compact: list[tuple[float, float]] = []
    for t, x in points:
        if not compact or abs(x - compact[-1][1]) >= 0.008 or t - compact[-1][0] >= 1.0:
            compact.append((t, x))
    with _cache_lock:
        _cache[key] = list(compact)
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
