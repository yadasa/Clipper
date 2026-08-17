from clipper.models import ClipCandidate, Word
from clipper.smartcut import KeepInterval, build_keep_intervals, compact_duration, remap_words


def test_long_silence_is_tightened_not_deleted_entirely_when_within_budget():
    candidate = ClipCandidate("c", 0, 12, 50, "x", transcript="hello world")
    words = [Word("hello", 0.5, 1.0), Word("world", 5.0, 5.5), Word("done", 10.0, 10.4)]
    intervals = build_keep_intervals(
        candidate,
        words,
        max_silence=0.7,
        retained_silence=0.14,
        remove_fillers=False,
        max_removed_ratio=0.9,
    )
    assert len(intervals) >= 2
    assert compact_duration(intervals) < candidate.duration
    assert compact_duration(intervals) > 1.0


def test_filler_cut_requires_surrounding_pause():
    candidate = ClipCandidate("c", 0, 5, 50, "x")
    no_pause = [Word("I", 0.0, 0.2), Word("um", 0.21, 0.4), Word("know", 0.41, 0.8)]
    intervals = build_keep_intervals(candidate, no_pause, max_silence=99, remove_fillers=True)
    assert len(intervals) == 1
    assert intervals[0].start == 0

    with_pause = [Word("I", 0.0, 0.2), Word("um", 0.45, 0.7), Word("know", 1.0, 1.3)]
    intervals = build_keep_intervals(candidate, with_pause, max_silence=99, remove_fillers=True)
    assert len(intervals) == 2


def test_remap_words_moves_later_words_onto_compact_timeline():
    candidate = ClipCandidate("c", 0, 8, 50, "x")
    words = [Word("one", 0.5, 0.8), Word("two", 5.0, 5.4)]
    intervals = build_keep_intervals(
        candidate,
        words,
        max_silence=0.7,
        retained_silence=0.1,
        remove_fillers=False,
        max_removed_ratio=0.9,
    )
    mapped = remap_words(words, intervals)
    assert [word.text for word in mapped] == ["one", "two"]
    # Test the actual invariant rather than coupling this regression to one
    # particular silence-retention constant: the later word must move earlier
    # by exactly the amount of timeline removed before it.
    removed_before_two = sum(
        max(0.0, min(interval.start, words[1].start) - previous_end)
        for previous_end, interval in zip(
            [candidate.start, *[item.end for item in intervals[:-1]]],
            intervals,
        )
        if interval.start < words[1].start
    )
    assert mapped[1].start < words[1].start
    assert mapped[1].start <= compact_duration(intervals)
    assert removed_before_two >= 0


def test_sparse_transcript_cannot_remove_more_than_damage_budget():
    candidate = ClipCandidate("c", 0, 20, 50, "x")
    words = [Word("only", 9.8, 10.2)]
    intervals = build_keep_intervals(candidate, words, max_removed_ratio=0.35)
    assert intervals == [KeepInterval(0, 20)]
    assert compact_duration(intervals) == 20
