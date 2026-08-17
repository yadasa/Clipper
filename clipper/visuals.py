from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from .config import Settings
from .models import VisualCue

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "Clipper/0.1 (local creator video editor)"


def _safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return value[:64] or "visual"


def pull_commons_image(query: str, output_dir: str | Path, index: int = 0) -> tuple[Path | None, dict]:
    """Pull a reusable image from Wikimedia Commons and save attribution metadata."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": query, "gsrnamespace": "6", "gsrlimit": "8",
        "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
    }
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(COMMONS_API, params=params)
        response.raise_for_status()
        pages = list((response.json().get("query", {}).get("pages", {}) or {}).values())
        pages.sort(key=lambda p: int(p.get("index", 9999)))
        candidates = []
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            mime = str(info.get("mime") or "")
            url = str(info.get("url") or "")
            if mime.startswith("image/") and url:
                candidates.append((page, info))
        if not candidates:
            return None, {}
        page, info = candidates[min(index, len(candidates) - 1)]
        suffix = Path(str(info.get("url"))).suffix.split("?")[0]
        if suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        path = out / f"commons-{_safe_name(query)}{suffix}"
        media = client.get(str(info["url"]))
        media.raise_for_status()
        path.write_bytes(media.content)
        meta = info.get("extmetadata") or {}
        attribution = {
            "source": "Wikimedia Commons",
            "page_title": page.get("title"),
            "description_url": info.get("descriptionurl"),
            "artist": (meta.get("Artist") or {}).get("value"),
            "license": (meta.get("LicenseShortName") or {}).get("value"),
            "license_url": (meta.get("LicenseUrl") or {}).get("value"),
        }
        path.with_suffix(path.suffix + ".json").write_text(json.dumps(attribution, indent=2), encoding="utf-8")
        return path, attribution


def generate_local_image(prompt: str, output_dir: str | Path, settings: Settings | None = None) -> Path:
    settings = settings or Settings()
    if not settings.diffusion_model:
        raise RuntimeError("DIFFUSION_MODEL must be set for local generated visuals")
    try:
        import torch
        from diffusers import DiffusionPipeline
    except ImportError as exc:
        raise RuntimeError("Install requirements-ai.txt to enable local image generation") from exc
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    pipeline = DiffusionPipeline.from_pretrained(settings.diffusion_model, torch_dtype=dtype)
    if torch.cuda.is_available():
        pipeline = pipeline.to("cuda")
    image = pipeline(prompt, num_inference_steps=24).images[0]
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"generated-{_safe_name(prompt[:80])}.png"
    image.save(path)
    return path


def resolve_visuals(cues: list[VisualCue], output_dir: str | Path, settings: Settings | None = None) -> list[VisualCue]:
    settings = settings or Settings()
    provider = settings.visual_provider
    for i, cue in enumerate(cues):
        path: Path | None = None
        if provider in {"commons", "auto"}:
            try:
                path, _ = pull_commons_image(cue.query or cue.transcript, Path(output_dir) / f"cue_{i:02d}")
            except Exception:
                path = None
        if path is None and provider in {"diffusers", "auto"} and settings.diffusion_model:
            try:
                path = generate_local_image(cue.prompt or cue.transcript, Path(output_dir) / f"cue_{i:02d}", settings)
            except Exception:
                path = None
        cue.asset_path = str(path) if path else None
    return cues
