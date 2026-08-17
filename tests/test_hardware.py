from clipper.config import Settings
from clipper.hardware import HardwareProfile, apply_profile_defaults


def _a4000():
    return HardwareProfile("nvidia-a4000", "NVIDIA RTX A4000", True, True, 16.0, "large-v3", 8, 2, "nvenc")


def test_a4000_profile_defaults_apply_without_overriding_explicit_env(monkeypatch, tmp_path):
    for name in ("WHISPER_MODEL", "WHISPER_BATCH_SIZE", "WHISPER_DEVICE", "FFMPEG_ENCODER", "RENDER_WORKERS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(workdir=tmp_path)
    apply_profile_defaults(settings, _a4000())
    assert settings.whisper_model == "large-v3"
    assert settings.whisper_device == "cuda"
    assert settings.whisper_batch_size == 8

    monkeypatch.setenv("WHISPER_MODEL", "medium")
    settings.whisper_model = "medium"
    apply_profile_defaults(settings, _a4000())
    assert settings.whisper_model == "medium"


def test_blank_env_values_still_allow_a4000_auto_tuning(monkeypatch, tmp_path):
    for name in ("WHISPER_MODEL", "WHISPER_BATCH_SIZE", "WHISPER_DEVICE", "FFMPEG_ENCODER", "RENDER_WORKERS"):
        monkeypatch.setenv(name, "")
    settings = Settings(workdir=tmp_path)
    apply_profile_defaults(settings, _a4000())
    assert settings.whisper_model == "large-v3"
    assert settings.whisper_device == "cuda"
    assert settings.whisper_batch_size == 8
    assert __import__("os").environ["FFMPEG_ENCODER"] == "nvenc"
    assert __import__("os").environ["RENDER_WORKERS"] == "2"
