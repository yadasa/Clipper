from clipper.models import Segment, SyncMap, Transcript
from clipper.multicam import _cut_ranges, _mapped_secondary_range


def test_secondary_range_maps_drift_and_offset_when_camera_covers_it():
    sync = SyncMap("camera2.mp4", intercept_seconds=1.0, rate=1.01, confidence=0.9)
    mapped = _mapped_secondary_range(10.0, 14.0, sync, camera_duration=30.0)
    assert mapped is not None
    start, end = mapped
    assert abs(start - (9.0 / 1.01)) < 1e-6
    assert abs(end - (13.0 / 1.01)) < 1e-6


def test_secondary_range_rejects_missing_head_or_tail_coverage():
    early = SyncMap("camera2.mp4", intercept_seconds=5.0, rate=1.0)
    assert _mapped_secondary_range(2.0, 6.0, early, camera_duration=30.0) is None

    short = SyncMap("camera2.mp4", intercept_seconds=0.0, rate=1.0)
    assert _mapped_secondary_range(8.0, 12.0, short, camera_duration=10.0) is None


def test_multicam_cut_ranges_follow_speech_boundaries():
    transcript = Transcript(
        text="a b c d e",
        language="en",
        duration=12.0,
        segments=[
            Segment(0.0, 2.0, "a"),
            Segment(2.0, 4.1, "b"),
            Segment(4.1, 6.2, "c"),
            Segment(6.2, 8.4, "d"),
            Segment(8.4, 10.5, "e"),
        ],
    )
    ranges = _cut_ranges(transcript, min_seconds=3.5, max_seconds=8.0)
    assert ranges[0] == (0.0, 4.1)
    assert ranges[1] == (4.1, 8.4)
    assert ranges[-1] == (8.4, 12.0)
