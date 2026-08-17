from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, capture_output=capture)


def require_executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"Required executable is missing from PATH: {name}")
    print(f"{name}: {path}")
    return path


def main() -> int:
    require_executable("nvidia-smi")
    require_executable("ffmpeg")
    require_executable("ffprobe")

    gpu = run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader",
        ],
        capture=True,
    ).stdout.strip()
    print("GPU:", gpu)
    if "A4000" not in gpu.upper():
        raise SystemExit(f"Expected an NVIDIA A4000 runner, got: {gpu}")

    try:
        import ctranslate2
    except Exception as exc:  # pragma: no cover - self-hosted GPU CI only
        raise SystemExit(f"CTranslate2 import failed: {exc}") from exc
    cuda_devices = int(ctranslate2.get_cuda_device_count())
    print(f"CTranslate2 CUDA device count: {cuda_devices}")
    if cuda_devices < 1:
        raise SystemExit("CTranslate2 cannot see a CUDA device")

    encoders = run(["ffmpeg", "-hide_banner", "-encoders"], capture=True).stdout
    if "h264_nvenc" not in encoders:
        raise SystemExit("FFmpeg does not expose h264_nvenc")

    with tempfile.TemporaryDirectory(prefix="clipper-gpu-smoke-") as tmp:
        root = Path(tmp)
        output = root / "nvenc-smoke.mp4"
        run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "1",
                "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
                "-cq", "25", "-b:v", "0",
                "-c:a", "aac", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(output),
            ]
        )
        if not output.is_file() or output.stat().st_size < 10_000:
            raise SystemExit("NVENC smoke render did not produce a valid-looking file")
        probe = run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height,pix_fmt",
                "-of", "default=noprint_wrappers=1", str(output),
            ],
            capture=True,
        ).stdout
        print(probe)
        required = ("codec_name=h264", "width=1280", "height=720", "pix_fmt=yuv420p")
        if not all(value in probe for value in required):
            raise SystemExit("NVENC smoke output failed ffprobe validation")

        # Device-count checks can succeed even when CUDA runtime libraries needed
        # for real inference are incomplete. Force one tiny faster-whisper model
        # through the same CTranslate2 CUDA path Clipper uses in production.
        audio = root / "whisper-smoke.wav"
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=16000",
            "-t", "1.2", "-ac", "1", "-ar", "16000", str(audio),
        ])
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("tiny.en", device="cuda", compute_type="float16")
            segments, info = model.transcribe(str(audio), beam_size=1, vad_filter=False)
            list(segments)  # Force lazy CTranslate2 inference.
            print(f"faster-whisper CUDA inference OK; language={getattr(info, 'language', 'unknown')}")
        except Exception as exc:
            raise SystemExit(f"Real faster-whisper CUDA inference failed: {exc}") from exc

    print("A4000 faster-whisper CUDA + NVENC smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
