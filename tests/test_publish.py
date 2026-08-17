from pathlib import Path

from clipper.publish import _idempotency_key, _normalized_platforms


def test_publish_idempotency_survives_path_move_for_same_media(tmp_path: Path):
    first = tmp_path / "a.mp4"
    second = tmp_path / "nested" / "b.mp4"
    second.parent.mkdir()
    payload = (b"rendered-video-content" * 10000) + b"tail"
    first.write_bytes(payload)
    second.write_bytes(payload)

    left = _idempotency_key(first, ["instagram"], title="Title", description="Caption")
    right = _idempotency_key(second, ["instagram"], title="Title", description="Caption")
    assert left == right


def test_publish_idempotency_changes_with_media_or_publish_intent(tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_bytes((b"video-v1" * 10000) + b"tail")
    base = _idempotency_key(media, ["instagram"], title="Title", description="Caption")

    changed_caption = _idempotency_key(media, ["instagram"], title="Title", description="Different")
    changed_platform = _idempotency_key(media, ["youtube"], title="Title", description="Caption")
    queued = _idempotency_key(media, ["instagram"], title="Title", description="Caption", add_to_queue=True)

    media.write_bytes((b"video-v2" * 10000) + b"tail")
    changed_media = _idempotency_key(media, ["instagram"], title="Title", description="Caption")

    assert len({base, changed_caption, changed_platform, queued, changed_media}) == 5


def test_x_alias_normalizes_to_twitter_once():
    assert _normalized_platforms(["x", "twitter", "X"]) == ["twitter"]
