from __future__ import annotations

import threading
from pathlib import Path

from .config import Settings
from .models import Segment, Transcript, Word

_model_lock = threading.Lock()
_model_cache: dict[tuple[str, str, str], object] = {}


def _device_and_compute(settings: Settings) -> tuple[str, str]:
    requested = settings.whisper_device.lower().strip()
    if requested not in {"", "auto"}:
        return requested, "float16" if requested == "cuda" else "int8"
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _get_model(settings: Settings):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt to enable transcription") from exc

    device, compute_type = _device_and_compute(settings)
    key = (settings.whisper_model, device, compute_type)
    if key not in _model_cache:
        with _model_lock:
            if key not in _model_cache:
                _model_cache[key] = WhisperModel(
                    settings.whisper_model,
                    device=device,
                    compute_type=compute_type,
                )
    return _model_cache[key], device


def transcribe(path: str | Path, settings: Settings | None = None) -> Transcript:
    settings = settings or Settings()
    model, device = _get_model(settings)

    kwargs = dict(word_timestamps=True, vad_filter=True, beam_size=5)
    segments_iter = None
    info = None
    if device == "cuda" and settings.whisper_batch_size > 1:
        try:
            from faster_whisper import BatchedInferencePipeline
            batched = BatchedInferencePipeline(model=model)
            segments_iter, info = batched.transcribe(
                str(path), batch_size=settings.whisper_batch_size, **kwargs
            )
        except Exception:
            segments_iter = None
    if segments_iter is None:
        segments_iter, info = model.transcribe(str(path), **kwargs)

    segments: list[Segment] = []
    full_text: list[str] = []
    for raw in segments_iter:
        text = (raw.text or "").strip()
        full_text.append(text)
        words = []
        for item in (raw.words or []):
            token = (item.word or "").strip()
            if token:
                words.append(Word(token, float(item.start), float(item.end)))
        segments.append(Segment(float(raw.start), float(raw.end), text, words))

    duration = max((s.end for s in segments), default=0.0)
    language = str(getattr(info, "language", "") or "unknown")
    return Transcript(" ".join(x for x in full_text if x), language, duration, segments)
