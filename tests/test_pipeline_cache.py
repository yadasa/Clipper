from pathlib import Path

from clipper.models import ClipCandidate, RenderedVariant
from clipper.pipeline import _load_cached_variants, _render_signature, _save_cached_variants


def test_render_signature_changes_when_logo_contents_change_in_place(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source" * 100_000)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"logo-v1")
    candidate = ClipCandidate("c", 0.0, 12.0, 88, "title", transcript="hello world")
    item = {"ratios": ["9:16"], "layout_modes": ["auto"], "caption_preset": "karaoke"}
    brand = {"name": "brand", "logo_path": str(logo), "accent": "#ffffff"}

    first = _render_signature(source, candidate, item, [], brand, "hook", None)
    logo.write_bytes(b"logo-v2-different")
    second = _render_signature(source, candidate, item, [], brand, "hook", None)

    assert first != second


def test_render_cache_rejects_tiny_or_truncated_variant(tmp_path: Path):
    cache_path = tmp_path / "render-cache.json"
    output = tmp_path / "bad.mp4"
    output.write_bytes(b"not-a-real-video")
    variant = RenderedVariant("c", "9:16", str(output), 1080, 1920)
    _save_cached_variants(cache_path, "sig", [variant])
    assert _load_cached_variants(cache_path, "sig") is None
