from clip_selection import build_transcript_windows, snap_clip_to_words


def test_windows_follow_segment_boundaries():
    transcript = {"segments": [
        {"start": 0, "end": 20, "text": "a"},
        {"start": 20, "end": 45, "text": "b"},
        {"start": 45, "end": 72, "text": "c"},
        {"start": 72, "end": 100, "text": "d"},
    ]}
    windows = build_transcript_windows(transcript, 100, window_seconds=60, overlap_seconds=20)
    assert windows[0]["start"] == 0
    assert windows[0]["end"] in {72.0, 100.0}
    assert all(window["end"] > window["start"] for window in windows)


def test_snap_clip_uses_word_edges():
    words = [
        {"w": "one", "s": 10.0, "e": 10.5},
        {"w": "two", "s": 11.0, "e": 11.5},
        {"w": "three", "s": 25.0, "e": 25.5},
        {"w": "four", "s": 26.0, "e": 26.5},
    ]
    start, end = snap_clip_to_words(10.2, 25.3, words, 30, min_duration=10, max_duration=20)
    assert start <= 10.0
    assert end >= 25.5
    assert 10 <= end - start <= 20
