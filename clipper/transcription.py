from __future__ import annotations

import threading
from pathlib import Path

from .config import Settings
from .models import Segment, Transcript, Word

_model_lock = threading.Lock()
_model_cache: dict[tuple[str, str, str], object] = {}
_batched_cache: dict[tuple[str, str, str], object] = {}


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


def _model_key(settings: Settings) -> tuple[str, str, str]:
    device, compute_type = _device_and_compute(settings)
    return settings.whisper_model, device, compute_type


def _get_model(settings: Settings):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt to enable transcription") from exc

    key = _model_key(settings)
    _, device, compute_type = key
    if key not in _model_cache:
        with _model_lock:
            if key not in _model_cache:
                _model_cache[key] = WhisperModel(
                    settings.whisper_model,
                    device=device,
                    compute_type=compute_type,
                )
    return _model_cache[key], device, key


def _get_batched_pipeline(model: object, key: tuple[str, str, str]):
    try:
        from faster_whisper import BatchedInferencePipeline
    except ImportError:
        return None
    if key not in _batched_cache:
        with _model_lock:
            if key not in _batched_cache:
                _batched_cache[key] = BatchedInferencePipeline(model=model)
    return _batched_cache[key]


def transcribe(path: str | Path, settings: Settings | None = None) -> Transcript:
    settings = settings or Settings()
    model, device, key = _get_model(settings)

    kwargs = dict(word_timestamps=True, vad_filter=True, beam_size=5)
    segments_iter = None
    info = None
    if device == "cuda" and settings.whisper_batch_size > 1:
        try:
            batched = _get_batched_pipeline(model, key)
            if batched is not None:
                segments_iter, info = batched.transcribe(
                    str(path), batch_size=settings.whisper_batch_size, **kwargs
                )
        except Exception:
            # Batching is an optimization. If a driver/runtime combination does
            # not support it, keep the same CUDA model and use normal inference.
            segments_iter = None
    if segments_iter is None:
        segments_iter, info = model.transcribe(str(path), **kwargs)

    segments: list[Segment] = []
    full_text: list[str] = []
    for raw in segments_iter:
        text = (raw.text or "").strip()
        full_text.append(text)
        words = []
        for item in raw.words or []:
            token = (item.word or "").strip()
            if token:
                words.append(Word(token, float(item.start), float(item.end)))
        segments.append(Segment(float(raw.start), float(raw.end), text, words))

    duration = max((segment.end for segment in segments), default=0.0)
    language = str(getattr(info, "language", "") or "unknown")
    return Transcript(" ".join(text for text in full_text if text), language, duration, segments)
