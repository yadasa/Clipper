from __future__ import annotations

import json
import math
import re
from dataclasses import asdict

from clip_selection import build_transcript_windows, clip_count_targets, snap_clip_to_words

from .config import Settings
from .models import ClipCandidate, Transcript, VisualCue

HOOK_WORDS = {
    "why", "how", "secret", "mistake", "never", "always", "best", "worst",
    "crazy", "actually", "truth", "problem", "fix", "learned", "nobody",
    "first", "biggest", "money", "cost", "free", "simple", "important",
}


def _window_score(text: str, duration: float) -> float:
    words = re.findall(r"[a-z0-9']+", text.lower())
    if not words:
        return 0.0
    hook_hits = sum(1 for word in words if word in HOOK_WORDS)
    question = 1.0 if "?" in text else 0.0
    numbers = min(3, len(re.findall(r"\b\d+(?:\.\d+)?\b", text)))
    density = min(1.0, len(words) / max(1.0, duration * 2.2))
    close = 1.0 if re.search(r"[.!?][\"']?\s*$", text.strip()) else 0.25
    return 2.0 * hook_hits + 1.25 * question + 0.5 * numbers + density + close


def rank_clips(transcript: Transcript, settings: Settings | None = None) -> list[ClipCandidate]:
    settings = settings or Settings()
    payload = transcript.selection_payload()
    windows = build_transcript_windows(payload, transcript.duration, window_seconds=70, overlap_seconds=25)
    low, high = clip_count_targets(len(windows))
    target = min(settings.max_clips, max(low, min(high, settings.max_clips)))
    words = [w.legacy() for w in transcript.words]

    scored: list[ClipCandidate] = []
    for index, window in enumerate(windows, 1):
        start = float(window["start"])
        end = float(window["end"])
        text = str(window["text"])
        # Prefer feed-sized moments. Long transcript windows are tightened to 58 s;
        # snapping then moves the boundaries to actual word edges.
        if end - start > 58:
            end = start + 58
        start, end = snap_clip_to_words(
            start, end, words, transcript.duration,
            min_duration=12, max_duration=60,
        )
        excerpt = " ".join(text.split()[:14])
        scored.append(ClipCandidate(
            id=f"clip_{index:03d}", start=start, end=end,
            score=_window_score(text, end - start),
            title=excerpt[:90] or f"Clip {index}",
            reason="Local transcript/window engagement heuristic",
            transcript=text,
        ))

    # Avoid near-duplicate windows while retaining chronologically distinct ideas.
    result: list[ClipCandidate] = []
    for item in sorted(scored, key=lambda c: c.score, reverse=True):
        overlap = False
        for chosen in result:
            intersect = max(0.0, min(item.end, chosen.end) - max(item.start, chosen.start))
            if intersect / max(1.0, min(item.duration, chosen.duration)) > 0.65:
                overlap = True
                break
        if not overlap:
            result.append(item)
        if len(result) >= target:
            break
    result.sort(key=lambda c: c.start)
    return result


def gemini_rerank(candidates: list[ClipCandidate], settings: Settings | None = None) -> list[ClipCandidate]:
    settings = settings or Settings()
    if not settings.gemini_api_key or not candidates:
        return candidates
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=settings.gemini_api_key)
        payload = [{"id": c.id, "start": c.start, "end": c.end, "text": c.transcript[:5000]} for c in candidates]
        prompt = (
            "Rank these potential short-form clips for a creator. Score 0-100 for hook, standalone clarity, "
            "information/emotion payoff, and likelihood viewers keep watching. Return JSON only: an array of "
            "objects with id, score, title, reason. Do not invent content. Candidates:\n" + json.dumps(payload)
        )
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
        )
        data = json.loads(response.text or "[]")
        ranked = {str(x.get("id")): x for x in data if isinstance(x, dict)}
        for candidate in candidates:
            if candidate.id in ranked:
                item = ranked[candidate.id]
                try:
                    candidate.score = float(item.get("score", candidate.score))
                except (TypeError, ValueError):
                    pass
                candidate.title = str(item.get("title") or candidate.title)[:120]
                candidate.reason = str(item.get("reason") or candidate.reason)[:500]
        return sorted(candidates, key=lambda c: c.score, reverse=True)
    except Exception:
        return candidates


def plan_visual_cues(candidate: ClipCandidate, settings: Settings | None = None) -> list[VisualCue]:
    settings = settings or Settings()
    text = candidate.transcript.strip()
    if not text:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        sentences = [text]
    duration = max(1.0, candidate.duration)
    cue_count = max(1, min(6, math.ceil(duration / 10)))
    cues: list[VisualCue] = []
    for i in range(cue_count):
        local_start = duration * i / cue_count
        local_end = min(duration, duration * (i + 1) / cue_count)
        sentence = sentences[min(len(sentences) - 1, int(i * len(sentences) / cue_count))]
        keywords = " ".join(re.findall(r"\b[A-Za-z0-9][\w'-]{3,}\b", sentence)[:8])
        cues.append(VisualCue(
            start=local_start, end=local_end,
            transcript=sentence,
            query=keywords or sentence[:100],
            prompt=f"Editorial b-roll image illustrating: {sentence[:400]}",
        ))

    if settings.gemini_api_key:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=settings.gemini_api_key)
            raw = client.models.generate_content(
                model=settings.gemini_model,
                contents=(
                    "Create b-roll/visual cues for this short-form clip. Return JSON array with start,end,query,prompt,modes. "
                    "Times are seconds from clip start. Use 3-8 second visuals; modes may be split,pip,interrupt. "
                    "Avoid a visual when speech is abstract and a cutaway would distract. Transcript:\n" + text[:14000]
                ),
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.3),
            )
            planned = json.loads(raw.text or "[]")
            parsed: list[VisualCue] = []
            for item in planned:
                if not isinstance(item, dict):
                    continue
                start = max(0.0, min(duration, float(item.get("start", 0))))
                end = max(start + 0.5, min(duration, float(item.get("end", start + 4))))
                modes = [m for m in item.get("modes", []) if m in {"split", "pip", "interrupt"}] or ["split", "pip", "interrupt"]
                parsed.append(VisualCue(start, end, str(item.get("transcript") or ""), str(item.get("query") or ""), str(item.get("prompt") or ""), modes))
            if parsed:
                cues = parsed
        except Exception:
            pass
    return cues
