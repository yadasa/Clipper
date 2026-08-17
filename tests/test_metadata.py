from clipper.metadata import local_social_metadata
from clipper.models import ClipCandidate


def test_local_social_metadata_is_platform_ready():
    candidate = ClipCandidate(
        "clip_001",
        0,
        30,
        80,
        "Why camera audio drifts",
        transcript="Camera audio can drift because separate recorders do not share the same clock. Sync anchors fix recorder drift.",
    )
    data = local_social_metadata(candidate)
    assert data["title"] == "Why camera audio drifts"
    assert data["hashtags"]
    assert len(data["platforms"]["twitter"]["caption"]) <= 280
    assert len(data["platforms"]["youtube"]["title"]) <= 100
