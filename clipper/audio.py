from __future__ import annotations

from pathlib import Path

from .media import probe


def has_audio(path: str | Path) -> bool:
    try:
        return any(stream.get("codec_type") == "audio" for stream in probe(path).get("streams", []))
    except Exception:
        return False


def music_input_args(path: str | Path | None) -> list[str]:
    if not path:
        return []
    music = Path(path).expanduser()
    if not music.is_file():
        return []
    return ["-stream_loop", "-1", "-i", str(music)]


def music_mix_filters(
    music_input_index: int,
    *,
    speech_label: str = "0:a",
    output_label: str = "aout",
    music_gain: float = 0.22,
) -> list[str]:
    """FFmpeg filters for a subtle music bed that ducks under speech."""
    gain = max(0.0, min(1.0, float(music_gain)))
    return [
        f"[{speech_label}]aresample=48000[speech]",
        f"[{music_input_index}:a]aresample=48000,volume={gain:.4f}[musicbase]",
        "[musicbase][speech]sidechaincompress=threshold=0.025:ratio=10:attack=15:release=260[ducked]",
        f"[speech][ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,loudnorm=I=-14:TP=-2.0:LRA=11[{output_label}]",
    ]
