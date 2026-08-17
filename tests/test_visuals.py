from pathlib import Path

import pytest

import clipper.visuals as visuals


class _Response:
    def __init__(self, *, payload=None, content=b"", headers=None):
        self._payload = payload or {}
        self.content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    def iter_bytes(self, _chunk_size):
        yield self.content


def _commons_payload():
    return {
        "query": {
            "pages": {
                "1": {
                    "index": 1,
                    "title": "File:Coffee.jpg",
                    "imageinfo": [{
                        "mime": "image/jpeg",
                        "url": "https://example.invalid/coffee.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Coffee.jpg",
                        "extmetadata": {},
                    }],
                }
            }
        }
    }


def test_commons_visual_is_reused_without_a_second_network_request(tmp_path: Path, monkeypatch):
    calls = {"api": 0, "media": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None):
            assert "w/api.php" in str(url)
            calls["api"] += 1
            return _Response(payload=_commons_payload())

        def stream(self, method, url):
            assert method == "GET"
            calls["media"] += 1
            return _Response(content=b"fake-jpeg-bytes")

    monkeypatch.setattr(visuals.httpx, "Client", FakeClient)
    first, _ = visuals.pull_commons_image("coffee beans", tmp_path)
    second, _ = visuals.pull_commons_image("coffee beans", tmp_path)

    assert first is not None and first.is_file()
    assert second == first
    assert calls == {"api": 1, "media": 1}


def test_commons_visual_rejects_declared_download_above_limit(tmp_path: Path, monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None):
            return _Response(payload=_commons_payload())

        def stream(self, method, url):
            return _Response(content=b"x", headers={"content-length": str(2 * 1024 * 1024)})

    monkeypatch.setattr(visuals.httpx, "Client", FakeClient)
    with pytest.raises(RuntimeError, match="limit"):
        visuals.pull_commons_image("coffee beans", tmp_path, max_bytes=1024 * 1024)
    assert not list(tmp_path.glob("*.part"))
