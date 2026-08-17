from __future__ import annotations

import os
from pathlib import Path

from clipper.analysis import rank_clips
from clipper.cache import file_fingerprint
from clipper.config import Settings
from clipper.edit_plan import _persist_asset
from clipper.models import ClipCandidate, Segment, Transcript, Word
from clipper.smartcut import build_keep_intervals


def test_smartcut_never_bridges_a_removed_gap_when_tiny_keep_segment_exists():
    candidate = ClipCandidate("c", 0.0, 5.0, 50, "x")
    words = [
        Word("opening", 0.0, 0.8),
        Word("tiny", 2.0, 2.1),
        Word("ending", 4.0, 4.5),
    ]
    intervals = build_keep_intervals(
        candidate,
        words,
        max_silence=0.30,
        retained_silence=0.04,
        remove_fillers=False,
        max_removed_ratio=0.90,
    )
    # The long silence around the tiny middle utterance was explicitly selected
    # for removal. A cleanup pass must never merge keep intervals by spanning
    # that deleted region and silently restore the dead air.
    assert not any(interval.start < 1.0 and interval.end > 1.9 for interval in intervals)


def test_fingerprint_is_stable_across_identical_copies_with_different_mtimes(tmp_path: Path):
    payload = (b"clipper-cache" * 200_000)[:2_400_000]
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(payload)
    second.write_bytes(payload)
    os.utime(first, (1_700_000_000, 1_700_000_000))
    os.utime(second, (1_800_000_000, 1_800_000_000))
    assert file_fingerprint(first) == file_fingerprint(second)


def test_fingerprint_detects_middle_of_large_file_change_even_when_size_and_mtime_match(tmp_path: Path):
    size = 3 * 1024 * 1024
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"A" * size)
    data = bytearray(b"A" * size)
    middle = size // 2
    data[middle - 4096 : middle + 4096] = b"B" * 8192
    second.write_bytes(data)
    timestamp = 1_750_000_000
    os.utime(first, (timestamp, timestamp))
    os.utime(second, (timestamp, timestamp))
    assert file_fingerprint(first) != file_fingerprint(second)


def test_ranked_candidate_transcript_never_contains_words_outside_its_trimmed_range(tmp_path: Path):
    segments = []
    token_times = {}
    for index in range(24):
        start = index * 5.0
        end = start + 4.0
        token = f"token{index:02d}"
        token_times[token] = start
        segments.append(Segment(start, end, token, [Word(token, start, end)]))
    transcript = Transcript(" ".join(token_times), "en", 120.0, segments)
    settings = Settings(workdir=tmp_path, max_clips=8)
    candidates = rank_clips(transcript, settings)
    assert candidates
    for candidate in candidates:
        included = set(candidate.transcript.split())
        assert included
        assert all(candidate.start <= token_times[token] < candidate.end for token in included)


def test_external_relative_asset_is_copied_inside_project_instead_of_escaping_root(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"logo")
    saved = _persist_asset("../outside.png", project, "logo")
    assert saved is not None
    assert saved.startswith("assets/")
    assert (project / saved).read_bytes() == b"logo"
