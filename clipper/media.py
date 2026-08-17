from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

SOCIAL_HOSTS = {
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com",
    "youtube.com", "www.youtube.com", "youtu.be",
    "facebook.com", "www.facebook.com",
    "x.com", "www.x.com", "twitter.com", "www.twitter.com",
}


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MediaError(f"Required executable not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise MediaError(detail[-3000:]) from exc


def probe(path: str | Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    return json.loads(result.stdout)


def duration(path: str | Path) -> float:
    data = probe(path)
    try:
        return float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def source_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]


def is_social_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in SOCIAL_HOSTS or any(host.endswith("." + item) for item in SOCIAL_HOSTS)


def copy_local_source(source: str | Path, destination_dir: str | Path) -> Path:
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise MediaError(f"Source file does not exist: {src}")
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower() or ".mp4"
    out = destination / f"source{suffix}"
    if src != out.resolve():
        shutil.copy2(src, out)
    return out


def download_owned_social_source(
    url: str,
    destination_dir: str | Path,
    *,
    own_content_ack: bool,
) -> Path:
    """Download the best source exposed by yt-dlp for media the user owns.

    This deliberately does not perform pixel-level watermark erasure. For TikTok
    and Instagram it asks the extractor for the highest-quality source it can
    access; logged-in cookies can expose an original/cleaner asset when the
    platform makes one available to the owner.
    """
    if is_social_url(url) and not own_content_ack:
        raise MediaError("Social-link import requires confirmation that you own or are authorized to reuse the content.")

    out_dir = Path(destination_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / "source.%(ext)s")
    cmd = [
        "yt-dlp", "--no-playlist", "--no-overwrites",
        "-f", "bv*+ba/b", "--merge-output-format", "mp4",
        "--remux-video", "mp4", "-o", template,
    ]
    cookies_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    cookies_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    elif cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    cmd.append(url)
    _run(cmd, timeout=None)

    candidates = sorted(out_dir.glob("source.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise MediaError("yt-dlp completed but no source file was produced")
    return candidates[0]


def ingest(source: str, destination_dir: str | Path, *, own_content_ack: bool = False) -> Path:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return download_owned_social_source(source, destination_dir, own_content_ack=own_content_ack)
    return copy_local_source(source, destination_dir)
