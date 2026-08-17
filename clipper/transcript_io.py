from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import Segment, Transcript, Word


def transcript_to_dict(transcript: Transcript) -> dict:
    return {
        "text": transcript.text,
        "language": transcript.language,
        "duration": transcript.duration,
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "words": [asdict(word) for word in segment.words],
            }
            for segment in transcript.segments
        ],
    }


def transcript_from_dict(data: dict) -> Transcript:
    segments: list[Segment] = []
    for raw in data.get("segments") or []:
        words = [
            Word(
                str(item.get("text") or item.get("w") or ""),
                float(item.get("start", item.get("s", 0))),
                float(item.get("end", item.get("e", 0))),
            )
            for item in raw.get("words") or []
        ]
        segments.append(
            Segment(
                float(raw.get("start", 0)),
                float(raw.get("end", 0)),
                str(raw.get("text") or ""),
                words,
            )
        )
    return Transcript(
        str(data.get("text") or ""),
        str(data.get("language") or "unknown"),
        float(data.get("duration") or max((s.end for s in segments), default=0.0)),
        segments,
    )


def save_transcript(transcript: Transcript, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(transcript_to_dict(transcript), indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def load_transcript(path: str | Path) -> Transcript:
    return transcript_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
