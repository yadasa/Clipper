from pathlib import Path

from clipper.automation import auto_edit_profile, transcript_from_words
from clipper.coherence import select_auto_clips
from clipper.config import Settings
from clipper.edit_plan import candidate_from_plan, generate_edit_plan, validate_edit_plan
from clipper.models import ClipCandidate, Transcript, Word
from clipper.quality import check_render


def _transcript() -> Transcript:
    words = []
    cursor = 0.0
    sections = [
        "Coffee roasting starts with green beans and careful heat.",
        "The first crack tells you the beans are changing quickly.",
        "I check airflow because too much smoke hides the flavor.",
        "Mortgage rates are a completely different topic today.",
        "Back to coffee, airflow and heat decide the final roast.",
        "That is why I cool the beans immediately after roasting.",
    ]
    for sentence in sections:
        for token in sentence.split():
            start = cursor
            cursor += 0.42
            words.append(Word(token, start, cursor - 0.04))
        cursor += 0.35
    return transcript_from_words(words, "en")


def test_auto_selector_returns_coherent_candidates_with_valid_ranges(tmp_path: Path):
    transcript = _transcript()
    settings = Settings(
        workdir=tmp_path,
        max_clips=4,
        auto_min_clip_seconds=6,
        auto_max_clip_seconds=30,
        auto_story_stitch=True,
    )
    clips = select_auto_clips(transcript, settings)
    assert clips
    assert len(clips) <= 4
    for clip in clips:
        assert clip.start >= 0
        assert clip.end <= transcript.duration + 0.001
        assert clip.duration >= 5.5
        assert clip.metrics["coherence"] >= 0
        if clip.source_intervals:
            assert len(clip.source_intervals) >= 2
            assert all(item["end"] > item["start"] for item in clip.source_intervals)


def test_edit_plan_round_trips_stitched_source_ranges(tmp_path: Path):
    candidate = ClipCandidate(
        "clip_001",
        1.0,
        15.0,
        88,
        "Story",
        transcript="one idea then the related payoff",
        source_intervals=[{"start": 1.0, "end": 5.0}, {"start": 10.0, "end": 15.0}],
    )
    plan = generate_edit_plan("project", [candidate], ["9:16"], __import__("clipper.brand", fromlist=["BrandKit"]).BrandKit())
    clean = validate_edit_plan(plan)
    rebuilt = candidate_from_plan(clean["clips"][0])
    assert rebuilt.source_intervals == candidate.source_intervals
    assert rebuilt.duration == 9.0


def test_auto_edit_profile_reduces_visual_noise_for_fast_delivery(tmp_path: Path):
    settings = Settings(workdir=tmp_path, broll_max_cues=6, caption_preset="karaoke")
    candidate = ClipCandidate(
        "c",
        0,
        30,
        85,
        "Fast",
        metrics={"pace": 90, "specificity": 30, "hook": 80, "payoff": 70},
    )
    profile = auto_edit_profile(candidate, settings)
    assert profile["caption_preset"] == "clean"
    assert profile["punch_ins"] is False
    assert 1 <= profile["broll_max_cues"] <= 6


def test_quality_gate_reports_missing_file():
    from clipper.models import RenderedVariant

    variant = RenderedVariant("c", "9:16", "/definitely/missing.mp4", 1080, 1920)
    check = check_render(variant, expected_duration=20, expect_audio=True)
    assert not check.ok
    assert "missing_or_tiny_file" in check.problems
