from __future__ import annotations

from pathlib import Path

from .brand import BrandKit
from .models import ClipCandidate, Word


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_escape(text: str) -> str:
    return str(text).replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def hex_to_ass(value: str, alpha: str = "00") -> str:
    raw = str(value or "#FFFFFF").lstrip("#")
    if len(raw) != 6:
        raw = "FFFFFF"
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    return f"&H{alpha}{bb}{gg}{rr}"


def _font_size(width: int, height: int, preset: str) -> int:
    if preset == "minimal":
        return max(34, int(height * 0.034))
    if preset == "clean":
        return max(40, int(height * 0.040))
    return max(44, int(height * 0.045))


def _margin_v(width: int, height: int) -> int:
    return int(height * (0.18 if height > width else 0.075))


def _word_groups(words: list[Word], size: int = 4) -> list[list[Word]]:
    return [words[i : i + size] for i in range(0, len(words), size) if words[i : i + size]]


def write_captions(
    words: list[Word],
    candidate: ClipCandidate,
    path: str | Path,
    width: int,
    height: int,
    brand: BrandKit,
    *,
    preset: str | None = None,
    hook_text: str | None = None,
    hook_seconds: float = 2.8,
) -> Path:
    preset = preset or brand.caption_preset
    local = [w for w in words if w.end >= candidate.start and w.start <= candidate.end]
    font_size = _font_size(width, height, preset)
    margin_v = _margin_v(width, height)
    primary = hex_to_ass(brand.primary_text)
    accent = hex_to_ass(brand.accent)
    outline = hex_to_ass(brand.outline)
    outline_size = 2 if preset == "minimal" else 4
    shadow = 0 if preset == "minimal" else 1
    hook_font_size = max(44, int(height * (0.050 if height > width else 0.055)))
    hook_margin_v = int(height * (0.075 if height > width else 0.045))
    hook_border_style = 3 if brand.hook_box else 1
    hook_back = hex_to_ass(brand.outline, "88") if brand.hook_box else "&H00000000"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{brand.font},{font_size},{primary},{accent},{outline},&H66000000,-1,0,0,0,100,100,0,0,1,{outline_size},{shadow},2,70,70,{margin_v},1
Style: Hook,{brand.font},{hook_font_size},{primary},{accent},{outline},{hook_back},-1,0,0,0,100,100,0,0,{hook_border_style},3,1,8,80,80,{hook_margin_v},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events: list[str] = [header]
    if hook_text:
        safe_hook = ass_escape(str(hook_text).strip())
        if safe_hook:
            end = min(candidate.duration, max(1.2, hook_seconds))
            events.append(f"Dialogue: 1,0:00:00.00,{ass_time(end)},Hook,,0,0,0,,{safe_hook}\n")

    for chunk in _word_groups(local, 4):
        chunk_start = max(0.0, chunk[0].start - candidate.start)
        chunk_end = min(candidate.duration, chunk[-1].end - candidate.start + 0.10)
        if preset != "karaoke":
            text = ass_escape(" ".join(w.text for w in chunk).upper())
            events.append(f"Dialogue: 0,{ass_time(chunk_start)},{ass_time(chunk_end)},Default,,0,0,0,,{text}\n")
            continue

        for active_index, active in enumerate(chunk):
            start = max(chunk_start, active.start - candidate.start)
            end = min(chunk_end, active.end - candidate.start + 0.06)
            parts = []
            for index, word in enumerate(chunk):
                color = accent if index == active_index else primary
                parts.append(f"{{\\c{color}}}{ass_escape(word.text.upper())}")
            events.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{' '.join(parts)}\n"
            )

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(events), encoding="utf-8")
    return out
