from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Iterable

import httpx

from .cache import file_fingerprint
from .config import Settings

UPLOAD_POST_URL = "https://api.upload-post.com/api/upload"
SUPPORTED_PLATFORMS = {
    "tiktok", "instagram", "youtube", "facebook", "linkedin", "twitter", "x",
    "threads", "pinterest", "bluesky", "reddit", "discord", "telegram",
    "google_business", "mastodon", "wordpress",
}

PREFERRED_RATIO = {
    "tiktok": "9:16", "instagram": "9:16", "youtube": "9:16", "threads": "9:16",
    "pinterest": "9:16", "facebook": "9:16", "bluesky": "9:16",
    "linkedin": "1:1", "twitter": "16:9", "x": "16:9", "reddit": "16:9",
    "discord": "16:9", "telegram": "16:9", "google_business": "1:1",
    "mastodon": "16:9", "wordpress": "16:9",
}


def _normalized_platforms(platforms: Iterable[str]) -> list[str]:
    chosen: list[str] = []
    for platform in platforms:
        value = platform.lower().strip()
        if value == "x":
            value = "twitter"
        if value not in SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported publishing platform: {platform}")
        if value not in chosen:
            chosen.append(value)
    if not chosen:
        raise ValueError("At least one platform is required")
    return chosen


def _idempotency_key(
    path: str | Path,
    platforms: Iterable[str],
    *,
    title: str = "",
    description: str = "",
    add_to_queue: bool = False,
) -> str:
    """Build a path-independent publish identity from media + publish intent.

    A retry after moving the same render should keep the same key, while changing
    the encoded media, caption, title, target platforms, or queue behavior should
    create a new request identity.
    """
    media = Path(path)
    chosen = _normalized_platforms(platforms)
    payload = "\0".join([
        "clipper-publish-v2",
        file_fingerprint(media, sample_bytes=1024 * 1024),
        ",".join(chosen),
        title.strip(),
        description.strip(),
        "queue" if add_to_queue else "publish-now",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class UploadPostPublisher:
    """Optional publishing adapter; credentials stay in local environment variables."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        if not self.settings.upload_post_api_key or not self.settings.upload_post_user:
            raise RuntimeError("UPLOAD_POST_API_KEY and UPLOAD_POST_USER are required")

    def upload_video(
        self,
        path: str | Path,
        platforms: Iterable[str],
        *,
        title: str = "",
        description: str = "",
        add_to_queue: bool = False,
    ) -> dict:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Publish file does not exist: {path}")
        chosen = _normalized_platforms(platforms)

        data: list[tuple[str, str]] = [("user", self.settings.upload_post_user)]
        data += [("platform[]", platform) for platform in chosen]
        if title:
            data.append(("title", title))
        if description:
            data.append(("description", description))
        if add_to_queue:
            data.append(("add_to_queue", "true"))
        idempotency = _idempotency_key(
            path,
            chosen,
            title=title,
            description=description,
            add_to_queue=add_to_queue,
        )
        headers = {
            "Authorization": f"Apikey {self.settings.upload_post_api_key}",
            "Idempotency-Key": idempotency,
        }
        mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
        with path.open("rb") as handle, httpx.Client(timeout=600) as client:
            response = client.post(
                UPLOAD_POST_URL,
                headers=headers,
                data=data,
                files={"video": (path.name, handle, mime)},
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"result": payload}


def choose_variant(variants: list[dict], platform: str) -> dict | None:
    desired = PREFERRED_RATIO.get(platform.lower(), "9:16")
    exact = [variant for variant in variants if variant.get("aspect_ratio") == desired]
    return (exact or variants or [None])[0]
