from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from clipper.automation import build_clean_master, choose_authoritative_audio, measure_audio_quality
from clipper.brand import BrandKit
from clipper.config import Settings
from clipper.models import ClipCandidate, Segment, SyncMap, Transcript, VisualCue, Word
from clipper.motion import PunchIn, apply_punch_ins
from clipper.multicam import build_multicam_master, replace_audio_with_synced_track
from clipper.quality import check_render
from clipper.render import render_clip
from clipper.smartcut import KeepInterval, build_keep_intervals, compact_duration, prepare_compacted_clip, remap_words


def run(cmd: list[str]) -> None:
    print("+", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(cmd, check=True)


def assert_video(path: Path, *, width: int | None = None, height: int | None = None) -> None:
    if not path.is_file() or path.stat().st_size < 5_000:
        raise SystemExit(f"Expected rendered video is missing or too small: {path}")
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,pix_fmt",
            "-of", "default=noprint_wrappers=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    print(probe)
    assert "codec_name=h264" in probe
    assert "pix_fmt=yuv420p" in probe
    if width is not None:
        assert f"width={width}" in probe
    if height is not None:
        assert f"height={height}" in probe


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="clipper-feature-smoke-") as tmp:
        root = Path(tmp)
        source = root / "source.mp4"
        silent_source = root / "silent-source.mp4"
        broll = root / "broll.mp4"
        music = root / "music.m4a"
        logo = root / "logo.png"
        mic = root / "mic.m4a"
        multicam_primary = root / "multicam-primary.mp4"
        multicam_secondary = root / "multicam-secondary.mp4"

        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=520:sample_rate=48000",
            "-t", "3.2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-pix_fmt", "yuv420p", str(source),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=24",
            "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-an", str(silent_source),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
            "-t", "0.8", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-an", str(broll),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=180:sample_rate=48000",
            "-t", "1", "-c:a", "aac", str(music),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=720:sample_rate=48000",
            "-t", "3.2", "-c:a", "aac", str(mic),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=white:s=180x70", "-frames:v", "1", str(logo),
        ])

        candidate = ClipCandidate(
            "smoke",
            0.0,
            3.2,
            80,
            "A smoke-test hook",
            transcript="This is actually important because the edit works.",
        )
        words = [
            Word("This", 0.05, 0.25),
            Word("is", 0.28, 0.40),
            Word("actually", 0.72, 0.94),
            Word("important", 1.22, 1.52),
            Word("because", 2.45, 2.65),
            Word("works.", 2.72, 2.98),
        ]
        transcript = Transcript(
            candidate.transcript,
            "en",
            3.2,
            [Segment(0.05, 2.98, candidate.transcript, words)],
        )

        # Auto-mode primitives: audio inspection + one global clean master before
        # any short-form candidate selection.
        primary_quality = measure_audio_quality(source, seconds=5)
        mic_quality = measure_audio_quality(mic, seconds=5)
        assert primary_quality.score > 0
        assert mic_quality.score > 0
        selected_audio, audio_decision = choose_authoritative_audio(source, mic)
        assert Path(selected_audio) in {source, mic}
        assert audio_decision["selected"] in {"primary", "external"}
        auto_settings = Settings(
            workdir=root / "auto-data",
            auto_global_cleanup=True,
            smart_cut=True,
            remove_fillers=True,
            auto_cleanup_max_removed_ratio=0.58,
        )
        clean_master = root / "auto-clean-master.mp4"
        clean_source, clean_transcript, clean_edl = build_clean_master(
            source,
            transcript,
            clean_master,
            auto_settings,
        )
        assert Path(clean_source).is_file()
        assert clean_transcript.duration <= transcript.duration
        assert clean_edl
        assert_video(Path(clean_source), width=640, height=360)

        intervals = build_keep_intervals(
            candidate,
            words,
            max_silence=0.55,
            retained_silence=0.12,
            remove_fillers=True,
        )
        assert compact_duration(intervals) < candidate.duration
        cut = root / "cut.mp4"
        prepare_compacted_clip(source, intervals, cut)
        mapped = remap_words(words, intervals)
        local = ClipCandidate(
            "smoke",
            0.0,
            compact_duration(intervals),
            80,
            candidate.title,
            transcript=candidate.transcript,
        )

        motion = root / "motion.mp4"
        apply_punch_ins(cut, [PunchIn(0.4, min(local.duration, 1.4), 1.08)], motion)

        brand = BrandKit(
            name="smoke",
            accent="#D6A77A",
            caption_preset="karaoke",
            logo_path=str(logo),
            logo_position="top-right",
        )
        visual_cues = [
            VisualCue(
                0.3,
                min(local.duration, 1.8),
                "important",
                "important concept",
                "",
                modes=["pip"],
                asset_path=str(broll),
                asset_type="video",
                provider="smoke",
            )
        ]
        output = root / "final.mp4"
        rendered = render_clip(
            motion,
            local,
            mapped,
            visual_cues,
            output,
            ratio="9:16",
            brand=brand,
            hook_text="SMOKE TEST HOOK",
            music_path=str(music),
        )
        assert_video(output, width=1080, height=1920)
        delivery_check = check_render(rendered, expected_duration=local.duration, expect_audio=True)
        assert delivery_check.ok, delivery_check.problems

        silent_cut = root / "silent-cut.mp4"
        prepare_compacted_clip(
            silent_source,
            [KeepInterval(0.0, 0.65), KeepInterval(1.0, 1.8)],
            silent_cut,
        )
        assert_video(silent_cut, width=480, height=270)
        silent_motion = root / "silent-motion.mp4"
        apply_punch_ins(silent_cut, [PunchIn(0.2, 0.8, 1.08)], silent_motion)
        assert_video(silent_motion, width=480, height=270)

        synced_audio = root / "synced-audio.mp4"
        replace_audio_with_synced_track(
            source,
            mic,
            SyncMap(str(mic), intercept_seconds=0.0, rate=1.0, confidence=1.0, method="smoke"),
            synced_audio,
        )
        assert_video(synced_audio, width=640, height=360)

        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "8.2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "31",
            "-c:a", "aac", "-pix_fmt", "yuv420p", str(multicam_primary),
        ])
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24",
            "-t", "8.2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "31",
            "-pix_fmt", "yuv420p", "-an", str(multicam_secondary),
        ])
        multicam_transcript = Transcript(
            text="first section second section",
            language="en",
            duration=8.0,
            segments=[
                Segment(0.0, 4.0, "first section"),
                Segment(4.0, 8.0, "second section"),
            ],
        )
        multicam = root / "multicam.mp4"
        build_multicam_master(
            multicam_primary,
            [(
                multicam_secondary,
                SyncMap(
                    str(multicam_secondary),
                    intercept_seconds=0.0,
                    rate=1.0,
                    confidence=1.0,
                    method="smoke",
                ),
            )],
            multicam_transcript,
            multicam,
        )
        assert_video(multicam, width=320, height=180)

        print("Clipper FFmpeg + auto-mode feature smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())