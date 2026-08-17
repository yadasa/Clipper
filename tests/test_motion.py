from clipper.models import Word
from clipper.motion import _segments, plan_punch_ins


def test_plan_punch_ins_is_sparse_and_bounded():
    words = [
        Word("this", 0.0, 0.3),
        Word("secret", 1.0, 1.3),
        Word("normal", 2.0, 2.3),
        Word("biggest", 7.0, 7.4),
        Word("mistake", 12.0, 12.4),
    ]
    events = plan_punch_ins(words, 15, min_gap=4.0)
    assert events
    assert all(0 <= event.start < event.end <= 15 for event in events)
    assert all(1.0 < event.scale <= 1.15 for event in events)
    assert all(b.start - a.start >= 4.0 for a, b in zip(events, events[1:]))


def test_motion_segments_cover_duration_without_gaps():
    from clipper.motion import PunchIn
    segments = _segments(10.0, [PunchIn(2, 4, 1.1), PunchIn(7, 8, 1.08)])
    assert segments[0][0] == 0
    assert segments[-1][1] == 10
    assert sum(end - start for start, end, _ in segments) == 10
    assert any(scale > 1 for _, _, scale in segments)
