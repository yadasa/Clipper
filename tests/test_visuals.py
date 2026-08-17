from pathlib import Path

import clipper.visuals as visuals


class _Response:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


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
            if "w/api.php" in str(url):
                calls["api"] += 1
                return _Response(payload={
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
                })
            calls["media"] += 1
            return _Response(content=b"fake-jpeg-bytes")

    monkeypatch.setattr(visuals.httpx, "Client", FakeClient)
    first, _ = visuals.pull_commons_image("coffee beans", tmp_path)
    second, _ = visuals.pull_commons_image("coffee beans", tmp_path)

    assert first is not None and first.is_file()
    assert second == first
    assert calls == {"api": 1, "media": 1}
