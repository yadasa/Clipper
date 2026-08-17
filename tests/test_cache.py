from pathlib import Path

from clipper.cache import StageCache, file_fingerprint, stable_hash


def test_stage_cache_invalidates_missing_artifact(tmp_path: Path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc" * 100)
    artifact = tmp_path / "out.mp4"
    artifact.write_bytes(b"fake")
    cache = StageCache(tmp_path / "cache")
    key = cache.key_for("stage", source, {"x": 1})
    cache.save("stage", key, {"value": 42}, [str(artifact)])
    assert cache.load("stage", key)["value"] == 42
    artifact.unlink()
    assert cache.load("stage", key) is None


def test_fingerprint_changes_when_file_changes(tmp_path: Path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"first")
    first = file_fingerprint(source)
    source.write_bytes(b"second-content")
    second = file_fingerprint(source)
    assert first != second


def test_stable_hash_ignores_dictionary_order():
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})
