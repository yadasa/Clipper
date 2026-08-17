from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .config import Settings
from .models import ClipCandidate, Transcript, Word
from .scoring import score_text

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}")
_STOP = {
    "about", "after", "again", "also", "because", "before", "being", "could", "does",
    "doing", "from", "have", "into", "just", "like", "more", "most", "other", "really",
    "should", "some", "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "very", "what", "when", "where", "which", "while", "with",
    "would", "your", "yeah", "okay", "right", "thing", "things",
}


@dataclass(slots=True, frozen=True)
class IdeaUnit:
    start: float
    end: float
    text: str
    terms: frozenset[str]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _terms(text: str) -> frozenset[str]:
    return frozenset(
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if token.lower() not in _STOP
    )


def _similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _idea_units(transcript: Transcript) -> list[IdeaUnit]:
    """Split the authoritative transcript into speech-sized idea units.

    Word timestamps are preferred because they survive the auto clean-master
    remap exactly. A unit closes on sentence punctuation, a meaningful pause, or
    once it becomes long enough to be independently useful.
    """
    words = transcript.words
    if not words:
        return [
            IdeaUnit(segment.start, segment.end, segment.text.strip(), _terms(segment.text))
            for segment in transcript.segments
            if segment.end > segment.start and segment.text.strip()
        ]

    units: list[IdeaUnit] = []
    bucket: list[Word] = []
    for index, word in enumerate(words):
        bucket.append(word)
        nxt = words[index + 1] if index + 1 < len(words) else None
        gap = max(0.0, nxt.start - word.end) if nxt else 99.0
        span = bucket[-1].end - bucket[0].start
        terminal = bool(re.search(r"[.!?][\"')\]]?$", word.text.strip()))
        close = terminal or gap >= 0.62 or span >= 9.0 or nxt is None
        if not close:
            continue
        text = " ".join(item.text for item in bucket).strip()
        if text and span >= 0.7:
            units.append(IdeaUnit(bucket[0].start, bucket[-1].end, text, _terms(text)))
        bucket = []
    return units


def _adaptive_targets(transcript: Transcript, settings: Settings) -> tuple[float, int]:
    duration = max(1.0, transcript.duration)
    wpm = len(transcript.words) / max(duration / 60.0, 1 / 60)
    if wpm >= 185:
        target_seconds = 24.0
    elif wpm >= 150:
        target_seconds = 31.0
    elif wpm >= 115:
        target_seconds = 38.0
    else:
        target_seconds = 46.0
    target_seconds = max(settings.auto_min_clip_seconds, min(settings.auto_max_clip_seconds, target_seconds))
    # Long sources should yield more opportunities; short sources should not be
    # forced into eight near-duplicates.
    target_count = max(1, math.ceil(duration / max(75.0, target_seconds * 2.2)))
    target_count = min(settings.max_clips, target_count)
    if duration >= 150:
        target_count = max(2, target_count)
    return target_seconds, target_count


def _boundary_score(text: str) -> float:
    stripped = text.strip()
    if not stripped:
        return 0.0
    start_bonus = 4.0 if re.match(r"^(so|here|the|this|when|if|first|one|you|i|we)\b", stripped, re.I) else 1.5
    end_bonus = 5.0 if re.search(r"[.!?][\"')\]]?$", stripped) else 1.0
    return start_bonus + end_bonus


def _coherence(units: list[IdeaUnit]) -> float:
    if len(units) <= 1:
        return 0.65
    values = [_similarity(left.terms, right.terms) for left, right in zip(units, units[1:])]
    # Neighboring spoken ideas rarely repeat every noun. Treat a modest lexical
    # bridge as strong evidence rather than requiring near-duplicate sentences.
    return min(1.0, 0.35 + sum(values) / len(values) * 2.2)


def _candidate_score(text: str, duration: float, units: list[IdeaUnit], target: float, joins: int) -> tuple[float, dict[str, float]]:
    metrics = score_text(text, duration)
    coherence = _coherence(units) * 100.0
    duration_fit = max(0.0, 100.0 - abs(duration - target) / max(target, 1.0) * 80.0)
    boundary = min(100.0, _boundary_score(text) * 10.0)
    overall = (
        metrics["overall"] * 0.68
        + coherence * 0.16
        + duration_fit * 0.10
        + boundary * 0.06
        - joins * 2.5
    )
    metrics = dict(metrics)
    metrics.update({
        "coherence": round(coherence, 2),
        "duration_fit": round(duration_fit, 2),
        "boundary": round(boundary, 2),
        "joins": float(joins),
        "overall": round(max(0.0, min(100.0, overall)), 2),
    })
    return metrics["overall"], metrics


def _merge_unit_intervals(units: list[IdeaUnit], max_gap: float = 0.9) -> list[dict[str, float]]:
    if not units:
        return []
    merged: list[list[float]] = [[units[0].start, units[0].end]]
    for unit in units[1:]:
        if unit.start - merged[-1][1] <= max_gap:
            merged[-1][1] = max(merged[-1][1], unit.end)
        else:
            merged.append([unit.start, unit.end])
    return [{"start": round(start, 4), "end": round(end, 4)} for start, end in merged]


def _continuous_candidates(units: list[IdeaUnit], settings: Settings, target: float) -> list[ClipCandidate]:
    results: list[ClipCandidate] = []
    minimum = float(settings.auto_min_clip_seconds)
    maximum = float(settings.auto_max_clip_seconds)
    serial = 1
    for start_index in range(len(units)):
        selected: list[IdeaUnit] = []
        for end_index in range(start_index, len(units)):
            selected.append(units[end_index])
            duration = selected[-1].end - selected[0].start
            if duration > maximum + 4:
                break
            if duration < minimum:
                continue
            # Keep two useful lengths per starting point: close to the adaptive
            # target and one slightly longer alternative.
            if abs(duration - target) > 7 and end_index + 1 < len(units) and duration < target + 8:
                continue
            text = " ".join(unit.text for unit in selected).strip()
            score, metrics = _candidate_score(text, duration, selected, target, joins=0)
            results.append(ClipCandidate(
                id=f"auto_{serial:03d}",
                start=selected[0].start,
                end=selected[-1].end,
                score=score,
                title=" ".join(text.split()[:14])[:100] or f"Auto clip {serial}",
                reason=f"Auto coherent span · coherence {metrics['coherence']:.0f} · duration fit {metrics['duration_fit']:.0f}",
                transcript=text,
                metrics=metrics,
            ))
            serial += 1
            if duration >= target + 7:
                break
    return results


def _stitched_candidates(units: list[IdeaUnit], settings: Settings, target: float, serial_start: int) -> list[ClipCandidate]:
    if not settings.auto_story_stitch:
        return []
    minimum = float(settings.auto_min_clip_seconds)
    maximum = float(settings.auto_max_clip_seconds)
    results: list[ClipCandidate] = []
    serial = serial_start

    for seed_index, seed in enumerate(units):
        chosen = [seed]
        topic = set(seed.terms)
        duration = seed.duration
        last_index = seed_index
        for index in range(seed_index + 1, min(len(units), seed_index + 11)):
            unit = units[index]
            if unit.start - seed.end > 110:
                break
            similarity = _similarity(frozenset(topic), unit.terms)
            shared = len(topic & set(unit.terms))
            if similarity < 0.10 and shared < 2:
                continue
            if duration + unit.duration > maximum:
                continue
            chosen.append(unit)
            topic.update(unit.terms)
            duration += unit.duration
            last_index = index
            if duration >= target * 0.85 and len(chosen) >= 2:
                break

        intervals = _merge_unit_intervals(chosen)
        if duration < minimum or len(intervals) < 2 or len(intervals) > 4:
            continue
        # Do not stitch wildly separated fragments even when a repeated noun
        # happens to match. The total envelope stays reasonably close to the
        # actual delivery length and all pieces remain chronological.
        envelope = units[last_index].end - seed.start
        if envelope > max(95.0, duration * 3.2):
            continue
        text = " ".join(unit.text for unit in chosen).strip()
        score, metrics = _candidate_score(text, duration, chosen, target, joins=len(intervals) - 1)
        if metrics["coherence"] < 45:
            continue
        results.append(ClipCandidate(
            id=f"story_{serial:03d}",
            start=intervals[0]["start"],
            end=intervals[-1]["end"],
            score=score,
            title=" ".join(text.split()[:14])[:100] or f"Story clip {serial}",
            reason=f"Auto story stitch · {len(intervals)} source slices · coherence {metrics['coherence']:.0f}",
            transcript=text,
            metrics=metrics,
            source_intervals=intervals,
        ))
        serial += 1
    return results


def _coverage(candidate: ClipCandidate) -> list[tuple[float, float]]:
    if candidate.source_intervals:
        return [(float(x["start"]), float(x["end"])) for x in candidate.source_intervals]
    return [(candidate.start, candidate.end)]


def _timeline_overlap(left: ClipCandidate, right: ClipCandidate) -> float:
    intersection = 0.0
    for a0, a1 in _coverage(left):
        for b0, b1 in _coverage(right):
            intersection += max(0.0, min(a1, b1) - max(a0, b0))
    return intersection / max(1.0, min(left.duration, right.duration))


def _text_similarity(left: str, right: str) -> float:
    return _similarity(_terms(left), _terms(right))


def select_auto_clips(transcript: Transcript, settings: Settings | None = None) -> list[ClipCandidate]:
    """Select coherent short-form clips from the cleaned authoritative timeline.

    The selector adapts clip length/count to source pace, evaluates sentence/idea
    boundaries, and may stitch a few chronological same-topic source ranges while
    skipping unrelated material between them. It never reorders speech.
    """
    settings = settings or Settings()
    units = _idea_units(transcript)
    if not units:
        return []
    target_seconds, target_count = _adaptive_targets(transcript, settings)
    pool = _continuous_candidates(units, settings, target_seconds)
    pool.extend(_stitched_candidates(units, settings, target_seconds, len(pool) + 1))
    pool.sort(key=lambda candidate: candidate.score, reverse=True)

    selected: list[ClipCandidate] = []
    for candidate in pool:
        if any(_timeline_overlap(candidate, other) > 0.66 for other in selected):
            continue
        if any(_text_similarity(candidate.transcript, other.transcript) > 0.72 for other in selected):
            continue
        selected.append(candidate)
        if len(selected) >= target_count:
            break

    if not selected and pool:
        selected = pool[:1]
    selected.sort(key=lambda candidate: candidate.start)
    for index, candidate in enumerate(selected, 1):
        candidate.id = f"clip_{index:03d}"
    return selected
