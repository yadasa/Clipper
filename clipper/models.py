from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Word:
    text: str
    start: float
    end: float

    def legacy(self) -> dict[str, Any]:
        return {"w": self.text, "s": self.start, "e": self.end}


@dataclass(slots=True)
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass(slots=True)
class Transcript:
    text: str
    language: str
    duration: float
    segments: list[Segment]

    @property
    def words(self) -> list[Word]:
        return [word for segment in self.segments for word in segment.words]

    def selection_payload(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in self.segments],
            "words": [w.legacy() for w in self.words],
        }


@dataclass(slots=True)
class ClipCandidate:
    id: str
    start: float
    end: float
    score: float
    title: str
    reason: str = ""
    transcript: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(slots=True)
class VisualCue:
    start: float
    end: float
    transcript: str
    query: str
    prompt: str
    modes: list[str] = field(default_factory=lambda: ["split", "pip", "interrupt"])
    asset_path: str | None = None


@dataclass(slots=True)
class SyncMap:
    secondary: str
    intercept_seconds: float
    rate: float = 1.0
    confidence: float = 0.0
    method: str = "waveform"
    anchors: int = 0

    def primary_time(self, secondary_time: float) -> float:
        return self.intercept_seconds + self.rate * secondary_time


@dataclass(slots=True)
class RenderedVariant:
    clip_id: str
    aspect_ratio: str
    path: str
    width: int
    height: int


@dataclass(slots=True)
class ProjectManifest:
    project_id: str
    source_path: str
    source_name: str
    created_at: str
    ratios: list[str]
    transcript_path: str | None = None
    clips: list[dict[str, Any]] = field(default_factory=list)
    status: str = "created"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def root(self) -> Path:
        return Path(self.source_path).parent
