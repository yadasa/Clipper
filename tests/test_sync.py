import numpy as np

from clipper.models import Segment, Transcript, Word
from clipper.sync import _correlation_lag, fit_transcript_sync


def test_waveform_lag_sign_maps_secondary_to_primary():
    primary = np.zeros(120, dtype=np.float32)
    secondary = np.zeros(120, dtype=np.float32)
    primary[20:27] = 1
    secondary[35:42] = 1
    lag, confidence = _correlation_lag(primary, secondary, 40)
    assert lag == -15
    assert confidence > 0


def _transcript(offset=0.0, rate=1.0):
    tokens = "this is the same spoken sentence used to align multiple camera recordings perfectly".split()
    words = [Word(token, offset + rate * i, offset + rate * i + 0.4) for i, token in enumerate(tokens)]
    return Transcript(" ".join(tokens), "en", words[-1].end, [Segment(words[0].start, words[-1].end, " ".join(tokens), words)])


def test_transcript_sync_recovers_offset_and_drift():
    primary = _transcript(offset=2.5, rate=1.001)
    secondary = _transcript(offset=0.0, rate=1.0)
    result = fit_transcript_sync(primary, secondary)
    assert result is not None
    assert abs(result.intercept_seconds - 2.5) < 0.05
    assert abs(result.rate - 1.001) < 0.001
    assert result.anchors >= 2
