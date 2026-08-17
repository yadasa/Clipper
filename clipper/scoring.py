from __future__ import annotations

import math
import re
from collections import Counter

from .models import ClipCandidate

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with",
    "is", "it", "that", "this", "you", "i", "we", "they", "he", "she", "was", "were",
    "are", "be", "been", "as", "at", "by", "from", "so", "if", "then", "just", "like",
}
_HOOK = {
    "why", "how", "secret", "mistake", "never", "always", "best", "worst", "truth",
    "problem", "fix", "learned", "biggest", "money", "cost", "free", "important", "crazy",
    "actually", "nobody", "first", "warning", "avoid", "stop", "start", "easy", "simple",
}
_PAYOFF = {
    "because", "therefore", "result", "means", "works", "worked", "solved", "fixed", "answer",
    "finally", "instead", "difference", "lesson", "takeaway", "point", "reason", "here's", "here",
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def topic_terms(text: str) -> set[str]:
    terms = {token for token in _tokens(text) if len(token) >= 4 and token not in _STOPWORDS}
    return terms


def topic_similarity(a: str, b: str) -> float:
    left, right = topic_terms(a), topic_terms(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def score_text(text: str, duration: float) -> dict[str, float]:
    tokens = _tokens(text)
    if not tokens or duration <= 0:
        return {key: 0.0 for key in ("hook", "clarity", "specificity", "payoff", "pace", "completeness", "overall")}

    count = len(tokens)
    starts = tokens[:18]
    hook_hits = sum(1 for t in starts if t in _HOOK)
    hook = min(100.0, 28.0 * hook_hits + (22.0 if "?" in text[:220] else 0.0) + min(28.0, len(starts) * 1.5))

    sentence_count = max(1, len(re.findall(r"[.!?]+", text)))
    avg_sentence = count / sentence_count
    filler_count = sum(1 for t in tokens if t in {"um", "uh", "erm", "hmm", "basically", "literally"})
    clarity = max(0.0, min(100.0, 92.0 - abs(avg_sentence - 15.0) * 1.7 - filler_count * 4.0))

    numbers = len(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text))
    properish = len(re.findall(r"\b[A-Z][a-z]{2,}\b", text))
    specificity = min(100.0, 20.0 + numbers * 15.0 + min(45.0, properish * 5.0))

    payoff_hits = sum(1 for t in tokens[-40:] if t in _PAYOFF)
    payoff = min(100.0, 22.0 + payoff_hits * 18.0 + (22.0 if re.search(r"[.!?][\"']?\s*$", text.strip()) else 0.0))

    words_per_second = count / max(duration, 0.01)
    # Conversational short-form speech tends to feel energetic around ~2.2–3.3 w/s.
    pace = max(0.0, 100.0 - abs(words_per_second - 2.65) * 42.0)

    opens_clean = bool(re.match(r"^[A-Z0-9\"']", text.strip()))
    closes_clean = bool(re.search(r"[.!?][\"']?\s*$", text.strip()))
    completeness = 35.0 + (25.0 if opens_clean else 0.0) + (40.0 if closes_clean else 0.0)

    overall = (
        hook * 0.24
        + clarity * 0.16
        + specificity * 0.15
        + payoff * 0.20
        + pace * 0.10
        + completeness * 0.15
    )
    return {
        "hook": round(hook, 2),
        "clarity": round(clarity, 2),
        "specificity": round(specificity, 2),
        "payoff": round(payoff, 2),
        "pace": round(pace, 2),
        "completeness": round(completeness, 2),
        "overall": round(overall, 2),
    }


def apply_scores(candidates: list[ClipCandidate]) -> list[ClipCandidate]:
    for candidate in candidates:
        candidate.metrics = score_text(candidate.transcript, candidate.duration)
        candidate.score = candidate.metrics["overall"]
        candidate.reason = (
            f"hook {candidate.metrics['hook']:.0f}, clarity {candidate.metrics['clarity']:.0f}, "
            f"specificity {candidate.metrics['specificity']:.0f}, payoff {candidate.metrics['payoff']:.0f}, "
            f"pace {candidate.metrics['pace']:.0f}, completeness {candidate.metrics['completeness']:.0f}"
        )
    return candidates


def diverse_top_candidates(
    candidates: list[ClipCandidate],
    limit: int,
    *,
    max_similarity: float = 0.56,
    min_score: float = 25.0,
) -> list[ClipCandidate]:
    """Greedy score-first selection with a topic-similarity guardrail.

    If diversity removes too many candidates, the best skipped items are backfilled
    so users still get the requested number when enough valid candidates exist.
    """
    ranked = sorted((c for c in candidates if c.score >= min_score), key=lambda c: c.score, reverse=True)
    selected: list[ClipCandidate] = []
    skipped: list[ClipCandidate] = []
    for candidate in ranked:
        if all(topic_similarity(candidate.transcript, chosen.transcript) <= max_similarity for chosen in selected):
            selected.append(candidate)
        else:
            skipped.append(candidate)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for candidate in skipped:
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) >= limit:
                break
    return selected[:limit]
