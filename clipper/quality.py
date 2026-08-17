from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .media import probe
from .models import RenderedVariant


@dataclass(slots=True, frozen=True)
class QualityCheck:
    ok: bool
    path: str
    problems: list[str]
    duration: float
    width: int
    height: int
    has_audio: bool

    def to_dict(self) -> dict:
        return asdict(self)


def check_render(
    variant: RenderedVariant,
    *,
    expected_duration: float,
    expect_audio: bool,
) -> QualityCheck:
    path = Path(variant.path)
    problems: list[str] = []
    if not path.is_file() or path.stat().st_size < 10_000:
        return QualityCheck(False, str(path), ["missing_or_tiny_file"], 0.0, 0, 0, False)

    try:
        info = probe(path)
    except Exception:
        return QualityCheck(False, str(path), ["ffprobe_failed"], 0.0, 0, 0, False)

    streams = info.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    try:
        duration = float((info.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)

    if video is None:
        problems.append("missing_video")
    if width != variant.width or height != variant.height:
        problems.append(f"wrong_dimensions:{width}x{height}")
    if expected_duration > 0:
        tolerance = max(0.8, expected_duration * 0.06)
        if abs(duration - expected_duration) > tolerance:
            problems.append(f"duration_mismatch:{duration:.3f}s")
    if expect_audio and audio is None:
        problems.append("missing_audio")
    pix_fmt = str((video or {}).get("pix_fmt") or "")
    if pix_fmt and pix_fmt not in {"yuv420p", "yuvj420p"}:
        problems.append(f"unexpected_pixel_format:{pix_fmt}")

    return QualityCheck(
        not problems,
        str(path),
        problems,
        round(duration, 4),
        width,
        height,
        audio is not None,
    )
