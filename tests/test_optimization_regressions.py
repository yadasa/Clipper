from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper.media import MediaError, _probe_cached, download_owned_social_source, is_social_url, probe
from clipper.models import ClipCandidate, RenderedVariant, VisualCue, Word
from clipper.pipeline import _prepare_candidate
from clipper.render import render_variants
from clipper.smartcut import build_keep_intervals


def test_remote_ingest_accepts_supported_social_hosts_only(tmp_path: Path):
    assert is_social_url("https://www.instagram.com/reel/example")
    assert is_social_url("https://vm.tiktok.com/example")
    assert not is_social_url("https://example.com/video.mp4")
    assert not is_social_url("file:///tmp/video.mp4")

    with pytest.raises(MediaError, match="supported social hosts"):
        download_owned_social_source(
            "https://example.com/video.mp4",
            tmp_path,
            own_content_ack=True,
        )


def test_probe_cache_avoids_repeated_ffprobe_and_invalidates_replaced_file(tmp_path: Path, monkeypatch):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"v1")
    calls = []

    def fake_run(cmd, *, timeout=None):
        calls.append((tuple(cmd), timeout))
        return SimpleNamespace(stdout='{"streams":[{"codec_type":"video","width":640,"height":360}],"format":{}}')

    import clipper.media as media_module

    _probe_cached.cache_clear()
    monkeypatch.setattr(media_module, "_run", fake_run)
    assert probe(media)["streams"][0]["width"] == 640
    assert probe(media)["streams"][0]["width"] == 640
    assert len(calls) == 1

    media.write_bytes(b"v2-with-a-different-size")
    assert probe(media)["streams"][0]["width"] == 640
    assert len(calls) == 2


def test_ambiguous_words_are_not_deleted_as_fillers():
    candidate = ClipCandidate("c", 0.0, 3.0, 80, "x")
    words = [
        Word("I", 0.20, 0.35),
        Word("actually", 1.00, 1.35),
        Word("won", 2.00, 2.25),
    ]
    intervals = build_keep_intervals(
        candidate,
        words,
        max_silence=5.0,
        remove_fillers=True,
    )
    assert len(intervals) == 1
    assert intervals[0].start == 0.0
    assert intervals[0].end == 3.0


def test_unambiguous_disfluency_can_still_be_removed():
    candidate = ClipCandidate("c", 0.0, 3.0, 80, "x")
    words = [
        Word("I", 0.20, 0.35),
        Word("um", 1.00, 1.25),
        Word("won", 2.00, 2.25),
    ]
    intervals = build_keep_intervals(
        candidate,
        words,
        max_silence=5.0,
        remove_fillers=True,
    )
    assert len(intervals) == 2
    assert intervals[0].end < intervals[1].start


def test_prepare_candidate_skips_noop_intermediate_encode(tmp_path: Path, monkeypatch):
    import clipper.pipeline as pipeline_module

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    candidate = ClipCandidate("c", 10.0, 12.0, 80, "x", transcript="plain speech")
    words = [Word("plain", 10.2, 10.6), Word("speech", 11.0, 11.5)]

    monkeypatch.setattr(pipeline_module, "plan_punch_ins", lambda *_args, **_kwargs: [])

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("no-op clip should not be encoded as an intermediate")

    monkeypatch.setattr(pipeline_module, "prepare_compacted_clip", should_not_run)
    monkeypatch.setattr(pipeline_module, "apply_punch_ins", should_not_run)

    render_source, render_candidate, render_words, intervals, punches = _prepare_candidate(
        source,
        candidate,
        words,
        {"smart_cut": False, "punch_ins": True, "remove_fillers": True},
        tmp_path / "project",
    )
    assert render_source == source
    assert render_candidate.start == candidate.start
    assert render_candidate.end == candidate.end
    assert render_words is words
    assert intervals == [{"start": 10.0, "end": 12.0}]
    assert punches == []


def test_render_batch_aligns_visual_cues_once(tmp_path: Path, monkeypatch):
    import clipper.render as render_module

    calls = {"align": 0, "render": 0}
    candidate = ClipCandidate("c", 0.0, 4.0, 80, "x")
    words = [Word("coffee", 0.2, 0.5)]
    cues = [VisualCue(0.0, 1.0, "coffee", "coffee", "")]

    def fake_align(values, _words, _candidate):
        calls["align"] += 1
        return list(values)

    def fake_render(_source, _candidate, _words, _cues, output_path, *, ratio, cues_aligned, **_kwargs):
        calls["render"] += 1
        assert cues_aligned is True
        width, height = {"9:16": (1080, 1920), "1:1": (1080, 1080)}[ratio]
        return RenderedVariant("c", ratio, str(output_path), width, height)

    monkeypatch.setattr(render_module, "align_visual_cues", fake_align)
    monkeypatch.setattr(render_module, "render_clip", fake_render)
    variants = render_variants(
        tmp_path / "source.mp4",
        candidate,
        words,
        cues,
        tmp_path / "out",
        ["9:16", "1:1"],
    )
    assert len(variants) == 2
    assert calls == {"align": 1, "render": 2}
