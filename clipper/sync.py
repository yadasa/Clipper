from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from .models import SyncMap, Transcript


def _audio_envelope(path: str | Path, sample_rate: int = 8000, envelope_hz: int = 50) -> np.ndarray:
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-f", "s16le", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required for audio sync") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.decode("utf-8", "ignore")[-2000:]) from exc
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    frame = max(1, sample_rate // envelope_hz)
    usable = (len(pcm) // frame) * frame
    if usable == 0:
        return np.zeros(1, dtype=np.float32)
    block = pcm[:usable].reshape(-1, frame)
    env = np.sqrt(np.mean(block * block, axis=1) + 1e-8)
    env -= np.mean(env)
    std = np.std(env)
    if std > 1e-8:
        env /= std
    return env.astype(np.float32)


def _next_pow2(n: int) -> int:
    return 1 << max(1, (n - 1).bit_length())


def _correlation_lag(a: np.ndarray, b: np.ndarray, max_lag: int | None = None) -> tuple[int, float]:
    """Return lag such that a-time ~= b-time + lag, plus normalized peak confidence."""
    if len(a) < 4 or len(b) < 4:
        return 0, 0.0
    n = _next_pow2(len(a) + len(b) - 1)
    corr = np.fft.irfft(np.fft.rfft(a, n) * np.fft.rfft(b[::-1], n), n)[: len(a) + len(b) - 1]
    lags = np.arange(-(len(b) - 1), len(a))
    if max_lag is not None:
        mask = np.abs(lags) <= max_lag
        corr = corr[mask]
        lags = lags[mask]
    idx = int(np.argmax(corr))
    denom = max(1.0, float(np.linalg.norm(a) * np.linalg.norm(b)))
    confidence = float(max(0.0, min(1.0, corr[idx] / denom)))
    return int(lags[idx]), confidence


def waveform_sync(primary: str | Path, secondary: str | Path, max_offset_seconds: float = 120.0) -> SyncMap:
    hz = 50
    a = _audio_envelope(primary, envelope_hz=hz)
    b = _audio_envelope(secondary, envelope_hz=hz)
    lag, confidence = _correlation_lag(a, b, int(max_offset_seconds * hz))
    return SyncMap(str(secondary), lag / hz, 1.0, confidence, "waveform", 0)


def _norm_word(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower())


def transcript_anchors(primary: Transcript, secondary: Transcript, ngram: int = 5) -> list[tuple[float, float]]:
    p = [(word.start, _norm_word(word.text)) for word in primary.words if _norm_word(word.text)]
    s = [(word.start, _norm_word(word.text)) for word in secondary.words if _norm_word(word.text)]
    if len(p) < ngram or len(s) < ngram:
        return []
    index: dict[tuple[str, ...], list[float]] = {}
    for i in range(len(p) - ngram + 1):
        key = tuple(word for _, word in p[i : i + ngram])
        index.setdefault(key, []).append(p[i][0])
    anchors: list[tuple[float, float]] = []
    for i in range(len(s) - ngram + 1):
        key = tuple(word for _, word in s[i : i + ngram])
        matches = index.get(key)
        if matches and len(matches) == 1:
            anchors.append((s[i][0], matches[0]))
    # De-duplicate anchors that come from overlapping n-grams.
    compact: list[tuple[float, float]] = []
    for pair in anchors:
        if not compact or pair[0] - compact[-1][0] >= 1.5:
            compact.append(pair)
    return compact


def fit_transcript_sync(primary: Transcript, secondary: Transcript, secondary_name: str = "secondary") -> SyncMap | None:
    anchors = transcript_anchors(primary, secondary)
    if len(anchors) < 2:
        return None
    x = np.array([anchor[0] for anchor in anchors], dtype=float)
    y = np.array([anchor[1] for anchor in anchors], dtype=float)

    # Robust two-stage fit: least squares, remove large residual outliers, refit.
    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (intercept + slope * x)
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median))) + 1e-6
    keep = np.abs(residual - median) <= max(0.35, 4.0 * mad)
    if int(np.sum(keep)) >= 2:
        slope, intercept = np.polyfit(x[keep], y[keep], 1)
        residual = y[keep] - (intercept + slope * x[keep])
    rms = float(np.sqrt(np.mean(residual * residual))) if len(residual) else 99.0
    confidence = float(max(0.0, min(1.0, 1.0 - rms / 1.5)))
    # Real recorder clock drift is small. Reject pathological text matches.
    if not 0.97 <= slope <= 1.03:
        return None
    return SyncMap(secondary_name, float(intercept), float(slope), confidence, "transcript", int(np.sum(keep)))


def estimate_sync(
    primary_path: str | Path,
    secondary_path: str | Path,
    primary_transcript: Transcript | None = None,
    secondary_transcript: Transcript | None = None,
) -> SyncMap:
    if primary_transcript is not None and secondary_transcript is not None:
        text_map = fit_transcript_sync(primary_transcript, secondary_transcript, str(secondary_path))
        if text_map and text_map.confidence >= 0.45:
            return text_map
    return waveform_sync(primary_path, secondary_path)


def ffmpeg_sync_filters(sync: SyncMap) -> tuple[str, str]:
    """FFmpeg filters mapping a secondary recording onto the primary timeline.

    rate maps secondary time -> primary time. Video uses setpts; audio uses
    asetrate+aresample for drift and adelay/atrim for the absolute offset.
    """
    rate = max(0.97, min(1.03, sync.rate))
    video = f"setpts={rate:.9f}*PTS"
    audio = f"asetrate=48000/{rate:.9f},aresample=48000"
    if sync.intercept_seconds >= 0:
        delay_ms = int(round(sync.intercept_seconds * 1000))
        video += f",setpts=PTS+{sync.intercept_seconds:.6f}/TB"
        audio += f",adelay={delay_ms}:all=1"
    else:
        trim = abs(sync.intercept_seconds)
        video += f",trim=start={trim:.6f},setpts=PTS-STARTPTS"
        audio += f",atrim=start={trim:.6f},asetpts=PTS-STARTPTS"
    return video, audio
