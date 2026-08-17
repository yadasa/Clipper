from pathlib import Path

from clipper.focus import crop_geometry, write_sendcmd


def test_crop_geometry_vertical_from_landscape():
    crop_w, crop_h = crop_geometry(1920, 1080, 1080, 1920)
    assert crop_h == 1080
    assert 606 <= crop_w <= 608
    assert crop_w % 2 == 0 and crop_h % 2 == 0


def test_crop_geometry_landscape_from_vertical():
    crop_w, crop_h = crop_geometry(1080, 1920, 1920, 1080)
    assert crop_w == 1080
    assert 606 <= crop_h <= 608
    assert crop_w % 2 == 0 and crop_h % 2 == 0


def test_sendcmd_clamps_crop_to_frame(tmp_path: Path):
    points = [(0.0, 0.0), (1.0, 0.5), (2.0, 1.0)]
    out = write_sendcmd(points, source_width=1920, crop_width=608, path=tmp_path / "focus.cmd")
    assert out is not None
    text = out.read_text()
    assert "crop@focus x 0;" in text
    assert "crop@focus x 656;" in text
    assert "crop@focus x 1312;" in text
