from clipper.audio import music_mix_filters, speech_intervals
from clipper.models import Word


def test_speech_intervals_merge_nearby_words_and_stay_clip_local():
    words = [
        Word("one", 10.2, 10.5),
        Word("two", 10.58, 10.9),
        Word("three", 12.0, 12.3),
    ]
    intervals = speech_intervals(words, clip_start=10.0, clip_duration=4.0)
    assert len(intervals) == 2
    assert intervals[0][0] == 0.1
    assert intervals[0][1] > 1.0
    assert intervals[1][0] > 1.8


def test_music_mix_filter_ducks_music_from_transcript_and_normalizes():
    words = [Word("hello", 0.2, 0.6), Word("world", 1.4, 1.8)]
    filters = music_mix_filters(3, speech_words=words, clip_duration=2.0, music_gain=0.2, duck_gain=0.05)
    joined = ";".join(filters)
    assert "[3:a]" in joined
    assert "volume='if(" in joined
    assert "between(t," in joined
    assert "0.0500" in joined
    assert "amix" in joined
    assert "loudnorm" in joined
    assert "sidechaincompress" not in joined


def test_music_mix_without_words_uses_conservative_constant_duck():
    joined = ";".join(music_mix_filters(2, speech_words=[]))
    assert "volume='0.0650':eval=frame" in joined
