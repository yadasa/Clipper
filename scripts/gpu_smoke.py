from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=capture,
    )


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
        import torch
    except Exception as exc:  # pragma: no cover - only runs on self-hosted GPU CI
        raise SystemExit(f"PyTorch import failed: {exc}") from exc

    if not torch.cuda.is_available():
        raise SystemExit("torch.cuda.is_available() is false")

    device_name = torch.cuda.get_device_name(0)
    total_gib = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    print(f"CUDA device: {device_name} ({total_gib:.1f} GiB)")
    if "A4000" not in device_name.upper():
        raise SystemExit(f"PyTorch is not seeing the expected A4000: {device_name}")

    encoders = run(["ffmpeg", "-hide_banner", "-encoders"], capture=True).stdout
    if "h264_nvenc" not in encoders:
        raise SystemExit("FFmpeg does not expose h264_nvenc")

    with tempfile.TemporaryDirectory(prefix="clipper-gpu-smoke-") as tmp:
        output = Path(tmp) / "nvenc-smoke.mp4"
        run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=30",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "1",
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p4",
                "-rc",
                "vbr",
                "-cq",
                "25",
                "-b:v",
                "0",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        if not output.is_file() or output.stat().st_size < 10_000:
            raise SystemExit("NVENC smoke render did not produce a valid-looking file")
        probe = run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "default=noprint_wrappers=1",
                str(output),
            ],
            capture=True,
        ).stdout
        print(probe)
        if "codec_name=h264" not in probe or "width=1280" not in probe or "height=720" not in probe:
            raise SystemExit("NVENC smoke output failed ffprobe validation")

    print("A4000 CUDA + NVENC smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
