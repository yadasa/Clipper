from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path

import httpx

from .config import Settings
from .models import VisualCue

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "Clipper/0.2 (local creator video editor)"
_SUPPORTED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp"}
_DIFFUSION_CACHE: dict[tuple[str, str], object] = {}
_DIFFUSION_LOCK = threading.Lock()


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return value[:64] or "visual"


def _cache_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:12]


def _read_attribution(path: Path) -> dict:
    sidecar = path.with_suffix(path.suffix + ".json")
    if not sidecar.is_file():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _stream_to_atomic_file(client: httpx.Client, url: str, path: Path, max_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.unlink(missing_ok=True)
    total = 0
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            declared = int(response.headers.get("content-length") or 0)
            if declared and declared > max_bytes:
                raise RuntimeError(
                    f"Commons image is {declared / (1024 * 1024):.1f} MB; "
                    f"limit is {max_bytes / (1024 * 1024):.0f} MB"
                )
            with temp.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError(
                            f"Commons image download exceeded {max_bytes / (1024 * 1024):.0f} MB"
                        )
                    handle.write(chunk)
        if total <= 0:
            raise RuntimeError("Commons image download was empty")
        os.replace(temp, path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _existing_commons_asset(out: Path, query: str, index: int) -> tuple[Path | None, dict]:
    safe = _safe_name(query)
    key = _cache_key(f"{query}\0{index}")
    for suffix in (".jpg", ".png", ".webp"):
        candidate = out / f"commons-{safe}-{key}{suffix}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate, _read_attribution(candidate)
    for suffix in (".jpg", ".png", ".webp"):
        candidate = out / f"commons-{safe}{suffix}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate, _read_attribution(candidate)
    return None, {}


def pull_commons_image(
    query: str,
    output_dir: str | Path,
    index: int = 0,
    *,
    max_bytes: int = 80 * 1024 * 1024,
) -> tuple[Path | None, dict]:
    """Pull a reusable raster image from Wikimedia Commons and cache it locally."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cached, attribution = _existing_commons_asset(out, query, index)
    if cached is not None:
        return cached, attribution

    max_bytes = max(1024 * 1024, int(max_bytes))
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": query, "gsrnamespace": "6", "gsrlimit": "8",
        "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
    }
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        response = client.get(COMMONS_API, params=params)
        response.raise_for_status()
        pages = list((response.json().get("query", {}).get("pages", {}) or {}).values())
        pages.sort(key=lambda page: int(page.get("index", 9999)))
        candidates = []
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            mime = str(info.get("mime") or "")
            url = str(info.get("url") or "")
            if mime in _SUPPORTED_IMAGE_MIME and url:
                candidates.append((page, info))
        if not candidates:
            return None, {}

        page, info = candidates[min(max(0, int(index)), len(candidates) - 1)]
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(str(info.get("mime")), ".jpg")
        key = _cache_key(f"{query}\0{index}")
        path = out / f"commons-{_safe_name(query)}-{key}{suffix}"
        _stream_to_atomic_file(client, str(info["url"]), path, max_bytes)

        meta = info.get("extmetadata") or {}
        attribution = {
            "source": "Wikimedia Commons",
            "page_title": page.get("title"),
            "description_url": info.get("descriptionurl"),
            "artist": (meta.get("Artist") or {}).get("value"),
            "license": (meta.get("LicenseShortName") or {}).get("value"),
            "license_url": (meta.get("LicenseUrl") or {}).get("value"),
        }
        sidecar = path.with_suffix(path.suffix + ".json")
        temp = sidecar.with_name(sidecar.name + ".tmp")
        temp.write_text(json.dumps(attribution, indent=2), encoding="utf-8")
        os.replace(temp, sidecar)
        return path, attribution


def _diffusion_pipeline(settings: Settings):
    if not settings.diffusion_model:
        raise RuntimeError("DIFFUSION_MODEL must be set for local generated visuals")
    try:
        import torch
        from diffusers import DiffusionPipeline
    except ImportError as exc:
        raise RuntimeError("Install requirements-ai.txt to enable local image generation") from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    key = (settings.diffusion_model, device)
    if key not in _DIFFUSION_CACHE:
        with _DIFFUSION_LOCK:
            if key not in _DIFFUSION_CACHE:
                dtype = torch.float16 if device == "cuda" else torch.float32
                pipeline = DiffusionPipeline.from_pretrained(settings.diffusion_model, torch_dtype=dtype)
                if device == "cuda":
                    pipeline = pipeline.to("cuda")
                else:
                    try:
                        pipeline.enable_attention_slicing()
                    except Exception:
                        pass
                _DIFFUSION_CACHE[key] = pipeline
    return _DIFFUSION_CACHE[key]


def generate_local_image(
    prompt: str,
    output_dir: str | Path,
    settings: Settings | None = None,
) -> Path:
    settings = settings or Settings()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    identity = _cache_key(f"{settings.diffusion_model}\0{prompt}\0steps=24")
    path = out / f"generated-{_safe_name(prompt[:80])}-{identity}.png"
    if path.is_file() and path.stat().st_size > 0:
        return path

    pipeline = _diffusion_pipeline(settings)
    image = pipeline(prompt, num_inference_steps=24).images[0]
    temp = path.with_name(path.stem + ".part.png")
    image.save(temp)
    os.replace(temp, path)
    return path


def resolve_visuals(
    cues: list[VisualCue],
    output_dir: str | Path,
    settings: Settings | None = None,
) -> list[VisualCue]:
    """Backward-compatible entry point for the multi-provider B-roll resolver."""
    from .broll import resolve_broll

    return resolve_broll(cues, output_dir, settings)
