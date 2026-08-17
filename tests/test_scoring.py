from clipper.models import ClipCandidate
from clipper.scoring import diverse_top_candidates, score_text, topic_similarity


def candidate(cid, text, score):
    return ClipCandidate(cid, 0, 25, score, cid, transcript=text, metrics={"overall": score})


def test_score_text_exposes_all_dimensions():
    metrics = score_text(
        "Why does this cost 30 percent less? Because the second method removes the expensive step.",
        12,
    )
    assert set(metrics) == {"hook", "clarity", "specificity", "payoff", "pace", "completeness", "overall"}
    assert 0 <= metrics["overall"] <= 100
    assert metrics["hook"] > 0
    assert metrics["specificity"] > 20


def test_topic_similarity_separates_different_ideas():
    same = topic_similarity(
        "camera autofocus settings for portrait video",
        "portrait camera autofocus settings explained",
    )
    different = topic_similarity(
        "camera autofocus settings for portrait video",
        "how to mix microphone audio and remove background noise",
    )
    assert same > different


def test_diverse_selection_avoids_near_duplicate_when_alternative_exists():
    items = [
        candidate("a", "camera autofocus settings portrait creator camera focus", 95),
        candidate("b", "portrait creator camera focus autofocus settings", 92),
        candidate("c", "microphone audio sync separate recorder waveform", 80),
    ]
    selected = diverse_top_candidates(items, 2, max_similarity=0.45)
    assert [item.id for item in selected] == ["a", "c"]
