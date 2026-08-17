from clipper.config import Settings
from clipper.hardware import HardwareProfile, apply_profile_defaults


def test_a4000_profile_defaults_apply_without_overriding_explicit_env(monkeypatch, tmp_path):
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.delenv("WHISPER_BATCH_SIZE", raising=False)
    monkeypatch.delenv("WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("FFMPEG_ENCODER", raising=False)
    monkeypatch.delenv("RENDER_WORKERS", raising=False)
    settings = Settings(workdir=tmp_path)
    profile = HardwareProfile("nvidia-a4000", "NVIDIA RTX A4000", True, True, 16.0, "large-v3", 8, 2, "nvenc")
    apply_profile_defaults(settings, profile)
    assert settings.whisper_model == "large-v3"
    assert settings.whisper_device == "cuda"
    assert settings.whisper_batch_size == 8

    monkeypatch.setenv("WHISPER_MODEL", "medium")
    settings.whisper_model = "medium"
    apply_profile_defaults(settings, profile)
    assert settings.whisper_model == "medium"
