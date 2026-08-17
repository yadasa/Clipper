from clipper.brand import load_brand, normalize_brand


def test_default_brand_honors_caption_preset_environment(monkeypatch):
    monkeypatch.setenv("CAPTION_PRESET", "minimal")
    assert load_brand(None).caption_preset == "minimal"


def test_bad_brand_values_fall_back_safely():
    kit = normalize_brand({
        "accent": "not-a-color",
        "primary_text": "#123456",
        "caption_preset": "chaos",
        "logo_position": "center",
    })
    assert kit.accent == "#D6A77A"
    assert kit.primary_text == "#123456"
    assert kit.caption_preset == "karaoke"
    assert kit.logo_position == "top-right"


def test_ass_font_name_cannot_break_style_csv_or_lines():
    kit = normalize_brand({"font": "Inter,Injected\nStyle: Evil"})
    assert kit.font == "Inter Injected Style: Evil"
    assert "," not in kit.font
    assert "\n" not in kit.font
    assert "\r" not in kit.font
