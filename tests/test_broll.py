from pathlib import Path

from clipper.analysis import plan_visual_cues
from clipper.broll import (
    LocalLibraryProvider,
    PexelsVideoProvider,
    PixabayVideoProvider,
    resolve_broll,
)
from clipper.config import Settings
from clipper.models import ClipCandidate, VisualCue, Word
from clipper.render import align_visual_cues


def test_local_library_resolves_semantically_related_asset(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    coffee = library / "coffee-beans-roasting.mp4"
    coffee.write_bytes(b"video" * 1000)
    (coffee.with_suffix(coffee.suffix + ".json")).write_text(
        '{"tags":["coffee","beans","roasting"],"creator":"owner","license":"owned"}',
        encoding="utf-8",
    )
    unrelated = library / "city-traffic.mp4"
    unrelated.write_bytes(b"video" * 1000)

    settings = Settings(
        workdir=tmp_path / "data",
        visual_provider="auto",
        broll_providers=("local",),
        broll_library_path=str(library),
        broll_min_relevance=0.2,
    )
    cue = VisualCue(1.0, 4.0, "We roast these coffee beans slowly.", "coffee beans roasting", "")
    resolved = resolve_broll([cue], tmp_path / "project" / "visuals", settings)

    assert resolved[0].provider == "local"
    assert resolved[0].asset_type == "video"
    assert Path(resolved[0].asset_path).is_file()
    assert "coffee" in Path(resolved[0].asset_path).name
    assert resolved[0].attribution["license"] == "owned"


def test_manual_broll_asset_is_never_replaced(tmp_path: Path):
    manual = tmp_path / "manual.mp4"
    manual.write_bytes(b"manual" * 1000)
    settings = Settings(
        workdir=tmp_path / "data",
        visual_provider="auto",
        broll_providers=("local",),
        broll_library_path=str(tmp_path / "missing"),
    )
    cue = VisualCue(
        0.5,
        2.5,
        "A manual visual",
        "anything",
        "",
        asset_path=str(manual),
        asset_type="video",
        provider="manual",
    )
    resolved = resolve_broll([cue], tmp_path / "project", settings)
    assert resolved[0].asset_path == str(manual)
    assert resolved[0].provider == "manual"


def test_visual_planner_uses_word_timestamps_for_spoken_ideas(tmp_path: Path):
    candidate = ClipCandidate(
        "c",
        0.0,
        12.0,
        80,
        "x",
        transcript=(
            "Fresh coffee beans roast slowly in the drum. "
            "Mortgage interest rates moved higher this morning."
        ),
    )
    words = [
        Word("Fresh", 0.3, 0.6),
        Word("coffee", 0.7, 1.0),
        Word("beans", 1.1, 1.4),
        Word("roast", 1.5, 1.8),
        Word("slowly", 1.9, 2.2),
        Word("in", 2.3, 2.4),
        Word("the", 2.5, 2.6),
        Word("drum.", 2.7, 3.1),
        Word("Mortgage", 7.9, 8.3),
        Word("interest", 8.4, 8.8),
        Word("rates", 8.9, 9.2),
        Word("moved", 9.3, 9.6),
        Word("higher", 9.7, 10.0),
        Word("this", 10.1, 10.3),
        Word("morning.", 10.4, 10.8),
    ]
    settings = Settings(
        workdir=tmp_path,
        visual_provider="auto",
        broll_auto_insert=True,
        broll_max_cues=4,
    )
    cues = plan_visual_cues(candidate, settings, words=words)
    assert len(cues) == 2
    assert cues[0].start < 0.5
    assert 2.8 <= cues[0].end <= 3.4
    assert 7.6 <= cues[1].start <= 8.1
    assert 10.5 <= cues[1].end <= 11.1


def test_render_alignment_snaps_semantic_cue_to_matching_spoken_words():
    candidate = ClipCandidate("c", 0.0, 12.0, 80, "x")
    words = [
        Word("Coffee", 0.2, 0.5),
        Word("is", 0.6, 0.8),
        Word("first.", 0.9, 1.3),
        Word("Mortgage", 7.5, 7.9),
        Word("interest", 8.0, 8.4),
        Word("rates", 8.5, 8.8),
        Word("moved", 8.9, 9.2),
        Word("higher.", 9.3, 9.7),
    ]
    cue = VisualCue(
        2.0,
        5.0,
        "Mortgage interest rates moved higher.",
        "mortgage interest rates",
        "",
    )
    aligned = align_visual_cues([cue], words, candidate)
    assert len(aligned) == 1
    assert 7.3 <= aligned[0].start <= 7.6
    assert 9.7 <= aligned[0].end <= 10.0


def test_pexels_prefers_practical_1080p_delivery_file():
    video = {
        "video_files": [
            {"id": 1, "link": "https://x/360.mp4", "file_type": "video/mp4", "width": 640, "height": 360},
            {"id": 2, "link": "https://x/1080.mp4", "file_type": "video/mp4", "width": 1920, "height": 1080},
            {"id": 3, "link": "https://x/4k.mp4", "file_type": "video/mp4", "width": 3840, "height": 2160},
        ]
    }
    assert PexelsVideoProvider._select_file(video)["id"] == 2


def test_pixabay_prefers_medium_stream():
    hit = {
        "videos": {
            "large": {"url": "https://x/large.mp4"},
            "medium": {"url": "https://x/medium.mp4"},
            "small": {"url": "https://x/small.mp4"},
        }
    }
    assert PixabayVideoProvider._select_stream(hit)["url"].endswith("medium.mp4")


def test_local_library_provider_indexes_once(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    asset = library / "coffee.mp4"
    asset.write_bytes(b"x" * 2048)
    settings = Settings(
        workdir=tmp_path / "data",
        broll_library_path=str(library),
    )
    provider = LocalLibraryProvider(settings)
    first = provider._index()
    asset.unlink()
    second = provider._index()
    assert first is second
    assert len(second) == 1
