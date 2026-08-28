from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class RemotionAvailability:
    available: bool
    node: str | None
    npm: str | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "node": self.node,
            "npm": self.npm,
            "reason": self.reason,
        }


def availability(editor_dir: str | Path | None = None) -> RemotionAvailability:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        return RemotionAvailability(False, node, npm, "Node.js and npm are required for Remotion rendering")
    if editor_dir is not None:
        package = Path(editor_dir) / "package.json"
        if not package.is_file():
            return RemotionAvailability(False, node, npm, f"Remotion editor package not found at {package}")
    return RemotionAvailability(True, node, npm)


def choose_render_backend(plan: dict[str, Any], *, force: str | None = None) -> str:
    """Choose the fastest renderer that still supports the requested composition.

    FFmpeg remains preferred for simple auto edits. Remotion is selected when the
    scene graph contains animation/graphics features that the fast path cannot
    faithfully reproduce.
    """
    requested = str(force or ((plan.get("scene_graph") or {}).get("render") or {}).get("preferred_backend") or "auto").lower()
    if requested in {"ffmpeg", "remotion"}:
        return requested
    graph = plan.get("scene_graph") if isinstance(plan.get("scene_graph"), dict) else {}
    items = graph.get("items") if isinstance(graph.get("items"), dict) else {}
    transitions = graph.get("transitions") if isinstance(graph.get("transitions"), dict) else {}
    rich_types = {"text", "shape", "lottie", "rive", "three", "sfx"}
    for item in items.values():
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        if str(item.get("type")) in rich_types:
            return "remotion"
        animations = item.get("animations")
        if isinstance(animations, dict) and any(values for values in animations.values() if isinstance(values, list)):
            return "remotion"
    if transitions:
        return "remotion"
    return "ffmpeg"


def render_with_remotion(
    plan_path: str | Path,
    output_path: str | Path,
    *,
    clip_id: str,
    ratio: str = "9:16",
    editor_dir: str | Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
    timeout_seconds: int = 60 * 30,
) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    editor = Path(editor_dir) if editor_dir is not None else repo_root / "editor"
    check = availability(editor)
    if not check.available:
        raise RuntimeError(check.reason)
    plan = Path(plan_path).resolve()
    output = Path(output_path).resolve()
    if not plan.is_file():
        raise FileNotFoundError(plan)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".part")
    temp.unlink(missing_ok=True)

    cmd = [
        check.npm or "npm",
        "--prefix",
        str(editor),
        "run",
        "render:plan",
        "--",
        "--plan",
        str(plan),
        "--clip",
        str(clip_id),
        "--ratio",
        str(ratio),
        "--output",
        str(temp),
    ]
    env = os.environ.copy()
    env.setdefault("REMOTION_ACKNOWLEDGE_LICENSE", "1")
    process = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        for raw in iter(process.stdout.readline, ""):
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    process.terminate()
                    raise
            line = raw.strip()
            if not line:
                continue
            if line.startswith("CLIPPER_PROGRESS ") and progress:
                try:
                    progress(json.loads(line.removeprefix("CLIPPER_PROGRESS ")))
                except (json.JSONDecodeError, TypeError):
                    pass
        return_code = process.wait(timeout=timeout_seconds)
        if return_code != 0:
            raise RuntimeError(f"Remotion renderer exited with status {return_code}")
        if not temp.is_file() or temp.stat().st_size < 10_000:
            raise RuntimeError("Remotion renderer did not produce a valid media file")
        os.replace(temp, output)
        return output
    finally:
        if process.poll() is None:
            process.kill()
        temp.unlink(missing_ok=True)
