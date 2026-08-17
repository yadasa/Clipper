from __future__ import annotations

import json
import re

from .config import Settings
from .models import ClipCandidate


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip(" \t\r\n-–—:;,.!?")
    return text


def local_hook(candidate: ClipCandidate, max_chars: int = 72) -> str:
    text = _clean(candidate.transcript)
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0] if text else ""
    title = _clean(candidate.title)
    options = [title, first]
    best = next((value for value in options if 8 <= len(value) <= max_chars), "")
    if not best:
        best = (title or first or "Worth watching")[:max_chars].rstrip()
    # Hooks read cleaner without terminal punctuation in an on-screen title card.
    return best.rstrip(".!?")


def generate_hook(candidate: ClipCandidate, settings: Settings | None = None) -> str:
    settings = settings or Settings()
    fallback = local_hook(candidate)
    if not settings.gemini_api_key:
        return fallback
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=(
                "Write one concise on-screen hook for this short clip. Maximum 9 words. "
                "It must be truthful to the transcript, specific, conversational, and not clickbait. "
                "Return JSON only: {\"hook\": \"...\"}. Transcript:\n" + candidate.transcript[:5000]
            ),
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.35),
        )
        data = json.loads(response.text or "{}")
        hook = _clean(data.get("hook", ""))
        words = hook.split()
        if 2 <= len(words) <= 11 and len(hook) <= 86:
            return hook.rstrip(".!?")
    except Exception:
        pass
    return fallback
