from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper.audio import _duck_expression
from clipper.media import MediaError, _probe_cached, download_owned_social_source, is_social_url, probe
from clipper.models import ClipCandidate, Word
from clipper.smartcut import build_keep_intervals


def test_missing_speech_timestamps_keep_normal_music_bed_level():
    assert _duck_expression([], normal_gain=0.22, duck_gain=0.065) == "0.2200"


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
