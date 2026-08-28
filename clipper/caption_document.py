from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import ClipCandidate, Transcript, Word


@dataclass(slots=True)
class CaptionToken:
    id: str
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None
    source: str = "transcript"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass(slots=True)
class CaptionPage:
    id: str
    start_ms: int
    end_ms: int
    token_ids: list[str]
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "token_ids": list(self.token_ids),
            "text": self.text,
        }


@dataclass(slots=True)
class CaptionDocument:
    id: str
    clip_id: str
    language: str | None
    text: str
    tokens: list[CaptionToken] = field(default_factory=list)
    pages: list[CaptionPage] = field(default_factory=list)
    source: str = "clipper-transcript"
    timing_quality: str = "word"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "language": self.language,
            "text": self.text,
            "tokens": [token.to_dict() for token in self.tokens],
            "pages": [page.to_dict() for page in self.pages],
            "source": self.source,
            "timing_quality": self.timing_quality,
        }


_PUNCT_NO_LEADING_SPACE = re.compile(r"^[,.;:!?%\)\]\}]$")
_PUNCT_NO_TRAILING_SPACE = re.compile(r"^[\(\[\{]$")


def _join_words(values: Iterable[str]) -> str:
    result = ""
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        if not result:
            result = value
        elif _PUNCT_NO_LEADING_SPACE.match(value):
            result += value
        elif _PUNCT_NO_TRAILING_SPACE.match(result[-1:]):
            result += value
        else:
            result += " " + value
    return result.strip()


def _clip_words(words: Iterable[Word], candidate: ClipCandidate) -> list[Word]:
    selected: list[Word] = []
    ranges = candidate.source_intervals or [{"start": candidate.start, "end": candidate.end}]
    for word in words:
        for interval in ranges:
            start = float(interval.get("start", 0))
            end = float(interval.get("end", 0))
            if word.end > start and word.start < end:
                selected.append(word)
                break
    return selected


def _map_source_time(candidate: ClipCandidate, source_seconds: float) -> float:
    """Map an absolute source timestamp onto a stitched clip-local timeline."""
    ranges = candidate.source_intervals or [{"start": candidate.start, "end": candidate.end}]
    cursor = 0.0
    for interval in ranges:
        start = float(interval.get("start", 0))
        end = float(interval.get("end", 0))
        if source_seconds < start:
            return cursor
        if source_seconds <= end:
            return cursor + max(0.0, source_seconds - start)
        cursor += max(0.0, end - start)
    return cursor


def _page_tokens(tokens: list[CaptionToken], *, max_chars: int = 34, max_tokens: int = 7, max_duration_ms: int = 2200) -> list[CaptionPage]:
    pages: list[CaptionPage] = []
    current: list[CaptionToken] = []

    def flush() -> None:
        if not current:
            return
        text = _join_words(token.text for token in current)
        pages.append(
            CaptionPage(
                id=f"page:{len(pages)}",
                start_ms=current[0].start_ms,
                end_ms=current[-1].end_ms,
                token_ids=[token.id for token in current],
                text=text,
            )
        )
        current.clear()

    for token in tokens:
        prospective = current + [token]
        text = _join_words(value.text for value in prospective)
        duration = prospective[-1].end_ms - prospective[0].start_ms
        if current and (len(text) > max_chars or len(prospective) > max_tokens or duration > max_duration_ms):
            flush()
        current.append(token)
        if re.search(r"[.!?][\"'’”)]?$", token.text) and len(current) >= 2:
            flush()
    flush()
    return pages


def from_transcript(transcript: Transcript, candidate: ClipCandidate) -> CaptionDocument:
    selected = _clip_words(transcript.words, candidate)
    tokens: list[CaptionToken] = []
    for index, word in enumerate(selected):
        local_start = _map_source_time(candidate, max(candidate.start, word.start))
        local_end = _map_source_time(candidate, min(candidate.end, word.end))
        if local_end <= local_start:
            continue
        tokens.append(
            CaptionToken(
                id=f"{candidate.id}:word:{index}",
                text=word.text,
                start_ms=max(0, int(round(local_start * 1000))),
                end_ms=max(1, int(round(local_end * 1000))),
            )
        )
    text = _join_words(token.text for token in tokens) or candidate.transcript
    return CaptionDocument(
        id=f"captions:{candidate.id}",
        clip_id=candidate.id,
        language=transcript.language,
        text=text,
        tokens=tokens,
        pages=_page_tokens(tokens),
        timing_quality="word" if tokens else "empty",
    )


def normalize_document(raw: dict[str, Any], *, clip_id: str | None = None) -> CaptionDocument:
    target_clip = str(raw.get("clip_id") or clip_id or "clip")
    raw_tokens = raw.get("tokens") if isinstance(raw.get("tokens"), list) else []
    tokens: list[CaptionToken] = []
    last_start = -1
    for index, value in enumerate(raw_tokens):
        if not isinstance(value, dict):
            continue
        try:
            start = max(0, int(value.get("start_ms", 0)))
            end = max(start + 1, int(value.get("end_ms", start + 1)))
        except (TypeError, ValueError):
            continue
        # Preserve chronological ordering while still allowing overlapping words.
        if start < last_start:
            start = last_start
            end = max(start + 1, end)
        last_start = start
        confidence = value.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        tokens.append(
            CaptionToken(
                id=str(value.get("id") or f"{target_clip}:word:{index}"),
                text=str(value.get("text") or "").strip(),
                start_ms=start,
                end_ms=end,
                confidence=confidence,
                source=str(value.get("source") or "edited"),
            )
        )
    text = str(raw.get("text") or _join_words(token.text for token in tokens)).strip()
    language = str(raw.get("language")) if raw.get("language") else None
    document = CaptionDocument(
        id=str(raw.get("id") or f"captions:{target_clip}"),
        clip_id=target_clip,
        language=language,
        text=text,
        tokens=tokens,
        source=str(raw.get("source") or "clipper-transcript"),
        timing_quality=str(raw.get("timing_quality") or ("word" if tokens else "empty")),
    )
    document.pages = _page_tokens(tokens)
    return document


def apply_text_edits(document: CaptionDocument, edits: dict[str, str]) -> CaptionDocument:
    """Change token text without disturbing timing for untouched words."""
    tokens = [
        CaptionToken(
            id=token.id,
            text=str(edits.get(token.id, token.text)).strip(),
            start_ms=token.start_ms,
            end_ms=token.end_ms,
            confidence=token.confidence,
            source="edited" if token.id in edits else token.source,
        )
        for token in document.tokens
    ]
    text = _join_words(token.text for token in tokens)
    return CaptionDocument(
        id=document.id,
        clip_id=document.clip_id,
        language=document.language,
        text=text,
        tokens=tokens,
        pages=_page_tokens(tokens),
        source=document.source,
        timing_quality=document.timing_quality,
    )


def _timestamp_srt(ms: int) -> str:
    ms = max(0, int(ms))
    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _timestamp_vtt(ms: int) -> str:
    return _timestamp_srt(ms).replace(",", ".")


def to_srt(document: CaptionDocument) -> str:
    pages = document.pages or _page_tokens(document.tokens)
    blocks: list[str] = []
    for index, page in enumerate(pages, start=1):
        blocks.append(
            f"{index}\n{_timestamp_srt(page.start_ms)} --> {_timestamp_srt(page.end_ms)}\n{page.text}\n"
        )
    return "\n".join(blocks).strip() + ("\n" if blocks else "")


def to_vtt(document: CaptionDocument) -> str:
    pages = document.pages or _page_tokens(document.tokens)
    blocks = ["WEBVTT", ""]
    for page in pages:
        blocks.extend(
            [
                f"{_timestamp_vtt(page.start_ms)} --> {_timestamp_vtt(page.end_ms)}",
                page.text,
                "",
            ]
        )
    return "\n".join(blocks).rstrip() + "\n"


def to_plain_text(document: CaptionDocument) -> str:
    return document.text.strip() + ("\n" if document.text.strip() else "")


def to_html_transcript(document: CaptionDocument) -> str:
    """Small safe HTML representation useful in local reports/previews."""
    return " ".join(
        f'<span data-start-ms="{token.start_ms}" data-end-ms="{token.end_ms}">{html.escape(token.text)}</span>'
        for token in document.tokens
    )
