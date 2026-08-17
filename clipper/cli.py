from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .pipeline import process_video
from .publish import PREFERRED_RATIO, UploadPostPublisher
from .worker import main as worker_main


def _process(args) -> None:
    manifest = process_video(
        args.source,
        ratios=args.ratio,
        own_content_ack=args.own_content,
        secondary_cameras=args.camera,
        external_audio=args.mic,
        alternate_visual_layouts=not args.no_alternates,
    )
    print(json.dumps(manifest.to_dict(), indent=2))


def _publish(args) -> None:
    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    publisher = UploadPostPublisher()
    platforms = [p.lower() for p in args.platform]
    for clip in data.get("clips", []):
        candidate = clip.get("candidate", {})
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
            result = publisher.upload_video(
                variant["path"], group,
                title=args.title or candidate.get("title", ""),
                description=args.description,
                add_to_queue=args.queue,
            )
            print(json.dumps({"clip": candidate.get("id"), "platforms": group, "result": result}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="clipper", description="Local-first automated short-form video editor")
    sub = parser.add_subparsers(dest="command", required=True)

    process = sub.add_parser("process", help="Clip and render a local file or authorized social URL")
    process.add_argument("source")
    process.add_argument("--ratio", action="append", choices=["9:16", "4:5", "1:1", "16:9"], default=[])
    process.add_argument("--camera", action="append", default=[], help="Additional synced camera recording; repeatable")
    process.add_argument("--mic", help="Separate microphone/audio recording")
    process.add_argument("--own-content", action="store_true", help="Confirm ownership/permission for a social URL")
    process.add_argument("--no-alternates", action="store_true", help="Render only the automatic visual composition")
    process.set_defaults(func=_process)

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
        # Rebuild argv so the worker module can keep a tiny standalone parser too.
        import sys
        sys.argv = [sys.argv[0]] + (["--once"] if args.once else [])
        worker_main()
        return
    args.func(args)


if __name__ == "__main__":
    main()
