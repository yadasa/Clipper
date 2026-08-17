from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from functools import lru_cache
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
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{cmd[0]} timed out after {timeout} seconds") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise MediaError(detail[-3000:]) from exc


@lru_cache(maxsize=256)
def _probe_cached(path: str, size: int, mtime_ns: int) -> str:
    """Cache ffprobe output while still invalidating files replaced in place."""
    del size, mtime_ns
    result = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", path,
    ], timeout=30)
    return result.stdout


def probe(path: str | Path) -> dict:
    target = Path(path).expanduser().resolve()
    try:
        stat = target.stat()
    except OSError as exc:
        raise MediaError(f"Media file does not exist or cannot be read: {target}") from exc
    try:
        payload = _probe_cached(str(target), int(stat.st_size), int(stat.st_mtime_ns))
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned invalid JSON for {target}") from exc
    return value if isinstance(value, dict) else {}


def duration(path: str | Path) -> float:
    data = probe(path)
    try:
        return float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def source_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]


def is_social_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    return host in SOCIAL_HOSTS or any(host.endswith("." + item) for item in SOCIAL_HOSTS)


def copy_local_source(
    source: str | Path,
    destination_dir: str | Path,
    *,
    prefer_hardlink: bool = False,
) -> Path:
    """Persist a local source inside a project without exposing partial copies.

    Normal creator files are copied so later edits cannot change an existing
    project's source by modifying the original. Disposable API/Firebase staging
    files may opt into a hard link: after the staging link is deleted, the project
    keeps the same immutable bytes without a second multi-gigabyte copy.
    """
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise MediaError(f"Source file does not exist: {src}")
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower() or ".mp4"
    out = destination / f"source{suffix}"
    resolved_out = out.resolve()
    if src == resolved_out:
        return out

    out.unlink(missing_ok=True)
    if prefer_hardlink:
        try:
            os.link(src, out)
            return out
        except OSError:
            out.unlink(missing_ok=True)

    temp = out.with_name(f"{out.stem}.part{out.suffix}")
    temp.unlink(missing_ok=True)
    try:
        shutil.copy2(src, temp)
        if not temp.is_file() or temp.stat().st_size != src.stat().st_size:
            raise MediaError(f"Source copy was incomplete: {src}")
        os.replace(temp, out)
        return out
    except Exception:
        temp.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        raise


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
    if not is_social_url(url):
        raise MediaError(
            "Remote ingest is limited to supported social hosts. "
            "Use a local file for other media until licensed direct-URL ingest is enabled."
        )
    if not own_content_ack:
        raise MediaError(
            "Social-link import requires confirmation that you own or are authorized to reuse the content."
        )

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

    candidates = sorted(
        (path for path in out_dir.glob("source.*") if path.is_file() and not path.name.endswith(".part")),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        raise MediaError("yt-dlp completed but no source file was produced")
    return candidates[0]


def ingest(
    source: str,
    destination_dir: str | Path,
    *,
    own_content_ack: bool = False,
    prefer_hardlink: bool = False,
) -> Path:
    parsed = urlparse(source)
    if parsed.scheme.lower() in {"http", "https"}:
        return download_owned_social_source(source, destination_dir, own_content_ack=own_content_ack)
    return copy_local_source(source, destination_dir, prefer_hardlink=prefer_hardlink)
