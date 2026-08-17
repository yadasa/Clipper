from __future__ import annotations

import json
import math
import re

from clip_selection import build_transcript_windows, clip_count_targets, snap_clip_to_words

from .config import Settings
from .models import ClipCandidate, Transcript, VisualCue, Word
from .scoring import diverse_top_candidates, score_text

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_TERM_RE = re.compile(r"\b[A-Za-z0-9][\w'-]{2,}\b")
_STOP = {
    "about", "after", "again", "also", "because", "before", "being", "could", "does",
    "doing", "from", "have", "into", "just", "like", "more", "most", "other", "really",
    "should", "some", "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "very", "what", "when", "where", "which", "while", "with",
    "would", "your",
}


def _text_for_range(transcript: Transcript, start: float, end: float, fallback: str) -> str:
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
                    candidate.metrics["ai"] = ai_score
                    candidate.score = round(
                        candidate.metrics.get("overall", candidate.score) * 0.45 + ai_score * 0.55,
                        2,
                    )
                except (TypeError, ValueError):
                    pass
                candidate.title = str(item.get("title") or candidate.title)[:120]
                candidate.reason = str(item.get("reason") or candidate.reason)[:500]
        return sorted(candidates, key=lambda c: c.score, reverse=True)
    except Exception:
        return candidates


def _visual_terms(text: str, limit: int = 8) -> str:
    terms: list[str] = []
    seen: set[str] = set()
    for token in _TERM_RE.findall(text):
        normalized = token.lower()
        if normalized in _STOP or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(token)
        if len(terms) >= limit:
            break
    return " ".join(terms)


def _local_words(words: list[Word], candidate: ClipCandidate) -> list[Word]:
    if not words:
        return []
    if candidate.start <= 0.001:
        return [
            Word(w.text, max(0.0, w.start), min(candidate.duration, w.end))
            for w in words
            if w.end > 0 and w.start < candidate.duration
        ]
    result: list[Word] = []
    for word in words:
        if word.end <= candidate.start or word.start >= candidate.end:
            continue
        start = max(0.0, word.start - candidate.start)
        end = min(candidate.duration, word.end - candidate.start)
        if end > start:
            result.append(Word(word.text, start, end))
    return result


def _timed_phrase_spans(words: list[Word], duration: float) -> list[tuple[float, float, str]]:
    if not words:
        return []
    spans: list[tuple[float, float, str]] = []
    bucket: list[Word] = []
    for index, word in enumerate(words):
        bucket.append(word)
        next_word = words[index + 1] if index + 1 < len(words) else None
        gap = max(0.0, next_word.start - word.end) if next_word else 99.0
        terminal = bool(re.search(r"[.!?][\"')\]]?$", word.text.strip()))
        long_enough = bucket[-1].end - bucket[0].start >= 2.5
        should_break = terminal or gap >= 0.55 or (long_enough and gap >= 0.28) or next_word is None
        if not should_break:
            continue
        text = " ".join(item.text for item in bucket).strip()
        start = max(0.0, bucket[0].start - 0.10)
        end = min(duration, bucket[-1].end + 0.20)
        if text and end - start >= 0.7:
            spans.append((start, end, text))
        bucket = []
    return spans


def _proportional_sentence_spans(text: str, duration: float) -> list[tuple[float, float, str]]:
    sentences = [item.strip() for item in _SENTENCE_RE.split(text) if item.strip()]
    if not sentences:
        sentences = [text.strip()] if text.strip() else []
    if not sentences:
        return []
    weights = [max(1, len(_TERM_RE.findall(sentence))) for sentence in sentences]
    total = sum(weights)
    cursor = 0.0
    spans = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        span = duration * weight / total
        start = cursor
        end = duration if index == len(sentences) - 1 else min(duration, cursor + span)
        spans.append((start, end, sentence))
        cursor = end
    return spans


def _select_visual_spans(
    spans: list[tuple[float, float, str]],
    max_cues: int,
    duration: float,
) -> list[tuple[float, float, str]]:
    candidates: list[tuple[float, int, float, float, str]] = []
    for index, (start, end, text) in enumerate(spans):
        terms = _visual_terms(text)
        if not terms:
            continue
        specificity = len(set(_TERM_RE.findall(terms)))
        has_number = 1 if re.search(r"\b\d+(?:[.,]\d+)?%?\b", text) else 0
        has_proper = 1 if re.search(r"\b[A-Z][a-z]{2,}\b", text) else 0
        score = specificity + has_number * 1.5 + has_proper * 0.5
        candidates.append((score, index, start, end, text))

    if not candidates:
        return []
    max_by_duration = max(1, math.ceil(duration / 8.0))
    count = min(max_cues, max_by_duration, len(candidates))
    chosen = sorted(candidates, key=lambda row: (-row[0], row[1]))[:count]
    chosen.sort(key=lambda row: row[2])

    result: list[tuple[float, float, str]] = []
    last_end = -1.0
    for _, _, start, end, text in chosen:
        if start < last_end - 0.15:
            start = last_end
        end = min(duration, max(start + 1.2, min(end, start + 7.5)))
        if end - start >= 1.0:
            result.append((start, end, text))
            last_end = end
    return result


def _fallback_visual_cues(
    candidate: ClipCandidate,
    settings: Settings,
    words: list[Word] | None,
) -> list[VisualCue]:
    local_words = _local_words(list(words or []), candidate)
    spans = (
        _timed_phrase_spans(local_words, candidate.duration)
        if local_words
        else _proportional_sentence_spans(candidate.transcript, candidate.duration)
    )
    selected = _select_visual_spans(spans, settings.broll_max_cues, candidate.duration)
    return [
        VisualCue(
            start=start,
            end=end,
            transcript=text,
            query=_visual_terms(text) or text[:100],
            prompt=f"Editorial B-roll illustrating this spoken idea: {text[:400]}",
        )
        for start, end, text in selected
    ]


def _normalize_ai_cues(planned: object, duration: float, max_cues: int) -> list[VisualCue]:
    if not isinstance(planned, list):
        return []
    parsed: list[VisualCue] = []
    last_end = 0.0
    for raw in planned:
        if not isinstance(raw, dict):
            continue
        try:
            start = max(0.0, min(duration, float(raw.get("start", 0))))
            end = max(start + 0.5, min(duration, float(raw.get("end", start + 4))))
        except (TypeError, ValueError):
            continue
        if start < last_end - 0.15:
            continue
        end = min(end, start + 8.0)
        if end - start < 1.0:
            continue
        transcript = str(raw.get("transcript") or "").strip()
        query = str(raw.get("query") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not query and transcript:
            query = _visual_terms(transcript)
        if not query:
            continue
        modes = [
            str(mode)
            for mode in raw.get("modes", [])
            if str(mode) in {"split", "pip", "interrupt"}
        ] or ["split", "pip", "interrupt"]
        parsed.append(VisualCue(
            start=start,
            end=end,
            transcript=transcript,
            query=query[:140],
            prompt=(prompt or f"Editorial B-roll illustrating: {transcript}")[:600],
            modes=modes,
        ))
        last_end = end
        if len(parsed) >= max_cues:
            break
    return parsed


def plan_visual_cues(
    candidate: ClipCandidate,
    settings: Settings | None = None,
    *,
    words: list[Word] | None = None,
) -> list[VisualCue]:
    """Plan context-aware B-roll windows on the exact clip timeline."""
    settings = settings or Settings()
    text = candidate.transcript.strip()
    if not text or not settings.broll_auto_insert or settings.visual_provider == "none":
        return []

    fallback = _fallback_visual_cues(candidate, settings, words)
    if not settings.gemini_api_key:
        return fallback

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        timestamp_context = ""
        local_words = _local_words(list(words or []), candidate)
        if local_words:
            timestamp_context = "\nTimed words:\n" + json.dumps(
                [{"w": w.text, "s": round(w.start, 2), "e": round(w.end, 2)} for w in local_words[:600]]
            )
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=(
                f"Plan at most {settings.broll_max_cues} B-roll insertions for this short-form clip. "
                "Return a JSON array with start,end,transcript,query,prompt,modes. Times are seconds from clip start. "
                "Place each visual exactly over the spoken idea it illustrates. Prefer concrete people, places, objects, "
                "actions, products, concepts with a recognizable visual, or factual context. Skip filler, transitions, "
                "and abstract speech where a cutaway would distract. Keep cues non-overlapping and usually 2.5-8 seconds. "
                "Queries should be concise stock-media search phrases, not full sentences. modes may be split,pip,interrupt. "
                "Do not invent claims absent from the transcript.\nTranscript:\n"
                + text[:14000]
                + timestamp_context
            ),
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.25),
        )
        ai_cues = _normalize_ai_cues(
            json.loads(response.text or "[]"),
            candidate.duration,
            settings.broll_max_cues,
        )
        return ai_cues or fallback
    except Exception:
        return fallback
