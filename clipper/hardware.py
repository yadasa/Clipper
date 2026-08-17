from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass


@dataclass(slots=True, frozen=True)
class HardwareProfile:
    name: str
    gpu_name: str | None
    cuda: bool
    nvenc: bool
    vram_gib: float | None
    whisper_model: str
    whisper_batch_size: int
    render_workers: int
    encoder: str

    def to_dict(self) -> dict:
        return asdict(self)


def _nvidia_info() -> tuple[str | None, float | None]:
    if not shutil.which("nvidia-smi"):
        return None, None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        first = result.stdout.strip().splitlines()[0]
        name, memory_mib = [part.strip() for part in first.rsplit(",", 1)]
        return name, float(memory_mib) / 1024.0
    except Exception:
        return None, None


def _cuda_available() -> bool:
    """Check the CUDA runtime used by faster-whisper first, then optional PyTorch.

    CTranslate2 ships with faster-whisper and is the relevant inference backend for
    Clipper's transcription path. This avoids making the large PyTorch package a
    mandatory base dependency just to detect the GPU.
    """
    try:
        import ctranslate2
        if int(ctranslate2.get_cuda_device_count()) > 0:
            return True
    except Exception:
        pass
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _nvenc_available() -> bool:
    try:
        from ffmpeg_utils import nvenc_available
        return bool(nvenc_available())
    except Exception:
        return False


def detect_hardware_profile() -> HardwareProfile:
    """Return conservative automatic defaults for the current workstation."""
    gpu_name, vram = _nvidia_info()
    cuda = _cuda_available()
    nvenc = _nvenc_available() if gpu_name else False
    upper = (gpu_name or "").upper()

    if cuda and "A4000" in upper:
        return HardwareProfile(
            name="nvidia-a4000",
            gpu_name=gpu_name,
            cuda=True,
            nvenc=nvenc,
            vram_gib=vram,
            whisper_model="large-v3",
            whisper_batch_size=8,
            render_workers=2,
            encoder="nvenc" if nvenc else "x264",
        )
    if cuda and (vram or 0) >= 12:
        return HardwareProfile(
            name="nvidia-12gb-plus",
            gpu_name=gpu_name,
            cuda=True,
            nvenc=nvenc,
            vram_gib=vram,
            whisper_model="large-v3",
            whisper_batch_size=6,
            render_workers=2,
            encoder="nvenc" if nvenc else "x264",
        )
    if cuda:
        return HardwareProfile(
            name="nvidia-cuda",
            gpu_name=gpu_name,
            cuda=True,
            nvenc=nvenc,
            vram_gib=vram,
            whisper_model="medium",
            whisper_batch_size=4,
            render_workers=2,
            encoder="nvenc" if nvenc else "x264",
        )
    return HardwareProfile(
        name="cpu",
        gpu_name=gpu_name,
        cuda=False,
        nvenc=False,
        vram_gib=vram,
        whisper_model="small",
        whisper_batch_size=1,
        render_workers=max(1, min(2, (os.cpu_count() or 2) // 4 or 1)),
        encoder="x264",
    )


def _explicit(name: str) -> bool:
    """Treat an empty .env assignment as unset so auto-tuning still works."""
    return bool(os.environ.get(name, "").strip())


def apply_profile_defaults(settings, profile: HardwareProfile | None = None):
    """Mutate Settings only for values the user did not explicitly configure.

    Blank values in ``.env`` are intentionally considered unset. This matters for
    the recommended template, where users may leave GPU tuning fields empty and
    let an RTX A4000 profile choose large-v3/CUDA/NVENC automatically.
    """
    profile = profile or detect_hardware_profile()
    if not _explicit("WHISPER_MODEL"):
        settings.whisper_model = profile.whisper_model
    if not _explicit("WHISPER_BATCH_SIZE"):
        settings.whisper_batch_size = profile.whisper_batch_size
    if not _explicit("WHISPER_DEVICE"):
        settings.whisper_device = "cuda" if profile.cuda else "cpu"
    if not _explicit("FFMPEG_ENCODER"):
        os.environ["FFMPEG_ENCODER"] = profile.encoder
    if not _explicit("RENDER_WORKERS"):
        os.environ["RENDER_WORKERS"] = str(profile.render_workers)
    return profile
