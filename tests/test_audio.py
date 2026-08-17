from clipper.audio import music_mix_filters


def test_music_mix_filter_ducks_music_under_speech_and_normalizes():
    filters = music_mix_filters(3, music_gain=0.2)
    joined = ";".join(filters)
    assert "[3:a]" in joined
    assert "sidechaincompress" in joined
    assert "amix" in joined
    assert "loudnorm" in joined
