from __future__ import annotations

import json
import math
import re

from clip_selection import build_transcript_windows, clip_count_targets, snap_clip_to_words

from .config import Settings
from .models import ClipCandidate, Transcript, VisualCue
from .scoring import diverse_top_candidates, score_text


def _text_for_range(transcript: Transcript, start: float, end: float, fallback: str) -> str:
    """Return only transcript words that actually overlap the selected clip.

    Transcript windows are intentionally wider than final social clips. Once a
    window is capped/snapped, using its original text leaks later speech into
    scoring, Gemini reranking, hooks, and B-roll planning. Prefer word timestamps
    whenever they are available so the intelligence layer judges the video the
    viewer will really see.
    """
    selected = [
        word.text
        for word in transcript.words
        if word.end > start and word.start < end and word.text.strip()
    ]
    return " ".join(selected).strip() or fallback.strip()


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
        window_text = str(window["text"]).strip()
        if end - start > 58:
            end = start + 58
        start, end = snap_clip_to_words(
            start, end, words, transcript.duration,
            min_duration=12, max_duration=60,
        )
        text = _text_for_range(transcript, start, end, window_text)
        metrics = score_text(text, end - start)
        excerpt = " ".join(text.split()[:14])
        scored.append(ClipCandidate(
            id=f"clip_{index:03d}",
            start=start,
            end=end,
            score=metrics["overall"],
            title=excerpt[:90] or f"Clip {index}",
            reason=(
                f"hook {metrics['hook']:.0f}, clarity {metrics['clarity']:.0f}, "
                f"specificity {metrics['specificity']:.0f}, payoff {metrics['payoff']:.0f}, "
                f"pace {metrics['pace']:.0f}, completeness {metrics['completeness']:.0f}"
            ),
            transcript=text,
            metrics=metrics,
        ))

    # First reject heavy timeline overlap, then reject near-duplicate topics. The
    # diversity selector backfills when necessary so users still receive enough clips.
    overlap_clean: list[ClipCandidate] = []
    for item in sorted(scored, key=lambda c: c.score, reverse=True):
        duplicate_timeline = False
        for chosen in overlap_clean:
            intersect = max(0.0, min(item.end, chosen.end) - max(item.start, chosen.start))
            if intersect / max(1.0, min(item.duration, chosen.duration)) > 0.65:
                duplicate_timeline = True
                break
        if not duplicate_timeline:
            overlap_clean.append(item)
    result = diverse_top_candidates(overlap_clean, target)
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
        payload = [
            {
                "id": c.id,
                "start": c.start,
                "end": c.end,
                "local_metrics": c.metrics,
                "text": c.transcript[:5000],
            }
            for c in candidates
        ]
        prompt = (
            "Rank these short-form clips for a creator. Score 0-100 for hook, standalone clarity, "
            "information/emotion payoff, and likelihood viewers keep watching. Local metrics are supplied as hints, "
            "but judge the transcript yourself. Return JSON only: an array of objects with id, score, title, reason. "
            "Do not invent content. Candidates:\n" + json.dumps(payload)
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
                    ai_score = max(0.0, min(100.0, float(item.get("score", candidate.score))))
                    # Blend AI judgement with deterministic local metrics so one
                    # malformed/outlier response cannot completely reorder a project.
                    candidate.metrics["ai"] = ai_score
                    candidate.score = round(candidate.metrics.get("overall", candidate.score) * 0.45 + ai_score * 0.55, 2)
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
            start=local_start,
            end=local_end,
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
                parsed.append(VisualCue(
                    start,
                    end,
                    str(item.get("transcript") or ""),
                    str(item.get("query") or ""),
                    str(item.get("prompt") or ""),
                    modes,
                ))
            if parsed:
                cues = parsed
        except Exception:
            pass
    return cues
