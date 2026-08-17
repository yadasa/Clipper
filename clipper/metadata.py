from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from .config import Settings
from .models import ClipCandidate

_STOP = {
    "this", "that", "with", "from", "have", "your", "about", "there", "their", "what",
    "when", "where", "which", "would", "could", "should", "just", "like", "because", "into",
    "they", "them", "then", "than", "been", "were", "will", "really", "very", "thing", "things",
}


def _keywords(text: str, limit: int = 6) -> list[str]:
    tokens = [
        token.lower()
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9'-]{3,}\b", text)
        if token.lower() not in _STOP
    ]
    counts = Counter(tokens)
    return [word for word, _ in counts.most_common(limit)]


def local_social_metadata(candidate: ClipCandidate) -> dict:
    title = candidate.title.strip()[:90] or "Short clip"
    excerpt = re.sub(r"\s+", " ", candidate.transcript).strip()
    if len(excerpt) > 240:
        excerpt = excerpt[:237].rsplit(" ", 1)[0] + "…"
    keywords = _keywords(candidate.transcript)
    hashtags = ["#" + re.sub(r"[^a-zA-Z0-9]", "", word.title()) for word in keywords if word]
    caption = excerpt
    if hashtags:
        caption = f"{caption}\n\n{' '.join(hashtags[:5])}".strip()
    return {
        "title": title,
        "caption": caption,
        "hashtags": hashtags[:8],
        "platforms": {
            "tiktok": {"caption": caption[:2200]},
            "instagram": {"caption": caption[:2200]},
            "youtube": {"title": title[:100], "description": caption[:5000]},
            "facebook": {"caption": caption[:5000]},
            "threads": {"caption": caption[:500]},
            "twitter": {"caption": caption[:280]},
            "linkedin": {"caption": caption[:3000]},
        },
    }


def generate_social_metadata(candidate: ClipCandidate, settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    fallback = local_social_metadata(candidate)
    if not settings.gemini_api_key:
        return fallback
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=(
                "Create truthful social metadata for this short video. Return JSON only with title, caption, hashtags. "
                "Title <= 90 chars. Caption should sound natural, not salesy. Hashtags must be relevant and max 8. "
                "Do not invent claims absent from the transcript. Transcript:\n" + candidate.transcript[:7000]
            ),
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.35),
        )
        raw = json.loads(response.text or "{}")
        title = str(raw.get("title") or fallback["title"]).strip()[:90]
        caption = str(raw.get("caption") or fallback["caption"]).strip()[:5000]
        hashtags = []
        for item in raw.get("hashtags") or []:
            tag = re.sub(r"[^A-Za-z0-9]", "", str(item).lstrip("#"))
            if tag and f"#{tag}" not in hashtags:
                hashtags.append(f"#{tag}")
        if hashtags and not any(tag.lower() in caption.lower() for tag in hashtags):
            caption = f"{caption}\n\n{' '.join(hashtags[:8])}".strip()
        result = local_social_metadata(candidate)
        result["title"] = title
        result["caption"] = caption
        result["hashtags"] = hashtags[:8] or fallback["hashtags"]
        result["platforms"]["tiktok"]["caption"] = caption[:2200]
        result["platforms"]["instagram"]["caption"] = caption[:2200]
        result["platforms"]["youtube"] = {"title": title[:100], "description": caption[:5000]}
        result["platforms"]["facebook"]["caption"] = caption[:5000]
        result["platforms"]["threads"]["caption"] = caption[:500]
        result["platforms"]["twitter"]["caption"] = caption[:280]
        result["platforms"]["linkedin"]["caption"] = caption[:3000]
        return result
    except Exception:
        return fallback


def extract_thumbnail(video_path: str | Path, output_path: str | Path, *, fraction: float = 0.32) -> Path:
    """Extract a representative high-quality JPEG from a finished variant."""
    source = Path(video_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(source)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        duration = max(0.1, float(probe.stdout.strip() or 0.1))
    except Exception:
        duration = 1.0
    timestamp = max(0.0, min(duration - 0.05, duration * max(0.05, min(0.9, fraction))))
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
        "-i", str(source), "-frames:v", "1", "-q:v", "2", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out
