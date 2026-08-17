import json
from pathlib import Path

import pytest

from clipper.brand import BrandKit
from clipper.edit_plan import candidate_from_plan, generate_edit_plan, load_edit_plan, save_edit_plan, validate_edit_plan
from clipper.models import ClipCandidate


def test_edit_plan_round_trip_and_manual_overrides(tmp_path: Path):
    candidate = ClipCandidate("clip_001", 10, 35, 88, "Original title", transcript="hello world", metrics={"overall": 88})
    plan = generate_edit_plan("project", [candidate], ["9:16"], BrandKit())
    plan["clips"][0]["start"] = 11.25
    plan["clips"][0]["end"] = 30.0
    plan["clips"][0]["caption_preset"] = "minimal"
    path = save_edit_plan(plan, tmp_path / "edit_plan.json")
    loaded = load_edit_plan(path)
    item = loaded["clips"][0]
    assert item["start"] == 11.25
    assert item["caption_preset"] == "minimal"
    rebuilt = candidate_from_plan(item)
    assert rebuilt.start == 11.25
    assert rebuilt.end == 30.0


def test_edit_plan_rejects_duplicate_ids():
    candidate = ClipCandidate("clip_001", 0, 20, 50, "x")
    plan = generate_edit_plan("project", [candidate], ["9:16"], BrandKit())
    plan["clips"].append(dict(plan["clips"][0]))
    with pytest.raises(ValueError):
        validate_edit_plan(plan)


def test_edit_plan_normalizes_bad_layout_and_caption_values():
    candidate = ClipCandidate("clip_001", 0, 20, 50, "x")
    plan = generate_edit_plan("project", [candidate], ["9:16"], BrandKit())
    plan["clips"][0]["layout_modes"] = ["garbage"]
    plan["clips"][0]["caption_preset"] = "neon-chaos"
    clean = validate_edit_plan(plan)
    assert clean["clips"][0]["layout_modes"] == ["auto"]
    assert clean["clips"][0]["caption_preset"] == "karaoke"
