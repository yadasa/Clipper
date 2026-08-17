from pathlib import Path

from clipper.brand import BrandKit
from clipper.captions import ass_escape, hex_to_ass, write_captions
from clipper.models import ClipCandidate, Word


def test_hex_to_ass_converts_rgb_to_bgr():
    assert hex_to_ass("#112233") == "&H00332211"


def test_ass_escape_collapses_line_breaks_and_escapes_override_braces():
    escaped = ass_escape("first\nsecond {danger}\\tag")
    assert "\n" not in escaped
    assert "\r" not in escaped
    assert "first second" in escaped
    assert r"\{danger\}" in escaped
    assert r"\\tag" in escaped


def test_karaoke_caption_file_contains_active_word_colors_and_hook(tmp_path: Path):
    words = [Word("hello", 0.0, 0.4), Word("world", 0.45, 0.9)]
    candidate = ClipCandidate("c", 0, 2, 50, "x")
    brand = BrandKit(accent="#D6A77A")
    out = write_captions(
        words,
        candidate,
        tmp_path / "captions.ass",
        1080,
        1920,
        brand,
        hook_text="THIS IS\nTHE HOOK",
    )
    text = out.read_text(encoding="utf-8")
    assert "Style: Hook" in text
    assert "THIS IS THE HOOK" in text
    assert hex_to_ass(brand.accent) in text
    assert text.count("Dialogue: 0") >= 2
