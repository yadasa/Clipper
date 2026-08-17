from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from clipper.brand import BrandKit
from clipper.models import ClipCandidate, VisualCue, Word
from clipper.motion import PunchIn, apply_punch_ins
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
        render_clip(
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

        print("Clipper FFmpeg feature smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
