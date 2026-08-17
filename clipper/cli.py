from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .hardware import detect_hardware_profile
from .pipeline import process_video, rerender_project
from .publish import PREFERRED_RATIO, UploadPostPublisher
from .worker import main as worker_main


def _settings_from_args(args) -> Settings:
    settings = Settings()
    if getattr(args, "brand", None):
        settings.brand_kit_path = args.brand
    if getattr(args, "music", None):
        settings.music_path = args.music
    if getattr(args, "no_smart_cut", False):
        settings.smart_cut = False
    if getattr(args, "keep_fillers", False):
        settings.remove_fillers = False
    if getattr(args, "no_punch_ins", False):
        settings.punch_ins = False
    if getattr(args, "no_hook", False):
        settings.hook_overlay = False
    if getattr(args, "no_cache", False):
        settings.stage_cache = False
    return settings


def _process(args) -> None:
    settings = _settings_from_args(args)
    manifest = process_video(
        args.source,
        ratios=args.ratio,
        own_content_ack=args.own_content,
        secondary_cameras=args.camera,
        external_audio=args.mic,
        alternate_visual_layouts=bool(args.alternates and not args.no_alternates),
        settings=settings,
    )
    print(json.dumps(manifest.to_dict(), indent=2))


def _rerender(args) -> None:
    settings = _settings_from_args(args)
    manifest = rerender_project(args.project, settings=settings)
    print(json.dumps(manifest.to_dict(), indent=2))


def _profile(_args) -> None:
    print(json.dumps(detect_hardware_profile().to_dict(), indent=2))


def _publish(args) -> None:
    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    publisher = UploadPostPublisher()
    platforms = [p.lower() for p in args.platform]
    for clip in data.get("clips", []):
        candidate = clip.get("candidate", {})
        metadata = clip.get("social_metadata") or {}
        variants = clip.get("variants", [])
        groups: dict[str, list[str]] = {}
        for platform in platforms:
            ratio = PREFERRED_RATIO.get(platform, "9:16")
            groups.setdefault(ratio, []).append(platform)
        for ratio, group in groups.items():
            matches = [v for v in variants if v.get("aspect_ratio") == ratio]
            variant = (matches or variants or [None])[0]
            if not variant:
                continue
            description = args.description or metadata.get("caption", "")
            result = publisher.upload_video(
                variant["path"],
                group,
                title=args.title or metadata.get("title") or candidate.get("title", ""),
                description=description,
                add_to_queue=args.queue,
            )
            print(json.dumps({"clip": candidate.get("id"), "platforms": group, "result": result}, indent=2))


def _add_edit_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--brand", help="Path to brand-kit JSON")
    parser.add_argument("--music", help="Optional background music file")
    parser.add_argument("--no-smart-cut", action="store_true", help="Do not tighten pauses")
    parser.add_argument("--keep-fillers", action="store_true", help="Keep filler words even when a clean cut is available")
    parser.add_argument("--no-punch-ins", action="store_true", help="Disable automatic emphasis zooms")
    parser.add_argument("--no-hook", action="store_true", help="Disable opening hook title overlay")
    parser.add_argument("--no-cache", action="store_true", help="Disable stage/render cache for this run")


def main() -> None:
    parser = argparse.ArgumentParser(prog="clipper", description="Local-first automated short-form video editor")
    sub = parser.add_subparsers(dest="command", required=True)

    process = sub.add_parser("process", help="Clip and render a local file or authorized social URL")
    process.add_argument("source")
    process.add_argument("--ratio", action="append", choices=["9:16", "4:5", "1:1", "16:9"], default=[])
    process.add_argument("--camera", action="append", default=[], help="Additional synced camera recording; repeatable")
    process.add_argument("--mic", help="Separate microphone/audio recording")
    process.add_argument("--own-content", action="store_true", help="Confirm ownership/permission for a social URL")
    process.add_argument("--alternates", action="store_true", help="Also render split/PIP/interruption alternatives")
    process.add_argument("--no-alternates", action="store_true", help=argparse.SUPPRESS)
    _add_edit_options(process)
    process.set_defaults(func=_process)

    rerender = sub.add_parser("rerender", help="Rerender an existing project's edit_plan.json without retranscribing")
    rerender.add_argument("project", help="Project directory or manifest.json path")
    _add_edit_options(rerender)
    rerender.set_defaults(func=_rerender)

    profile = sub.add_parser("profile", help="Show the detected local hardware tuning profile")
    profile.set_defaults(func=_profile)

    publish = sub.add_parser("publish", help="Publish rendered clips through configured Upload-Post account")
    publish.add_argument("manifest")
    publish.add_argument("--platform", action="append", required=True)
    publish.add_argument("--title", default="")
    publish.add_argument("--description", default="")
    publish.add_argument("--queue", action="store_true")
    publish.set_defaults(func=_publish)

    worker = sub.add_parser("worker", help="Run the Firebase home-desktop worker")
    worker.add_argument("--once", action="store_true")
    worker.set_defaults(func=None)

    args = parser.parse_args()
    if args.command == "worker":
        import sys
        sys.argv = [sys.argv[0]] + (["--once"] if args.once else [])
        worker_main()
        return
    args.func(args)


if __name__ == "__main__":
    main()
