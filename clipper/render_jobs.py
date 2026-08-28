from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"done", "failed", "cancelled"}
ACTIVE_STATES = {"queued", "preparing", "rendering", "encoding", "muxing", "uploading"}


@dataclass(slots=True)
class RenderProgress:
    stage: str = "queued"
    progress: float = 0.0
    rendered_frames: int = 0
    encoded_frames: int = 0
    frame_count: int = 0
    detail: str = ""
    started_at: float | None = None
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "progress": max(0.0, min(1.0, float(self.progress))),
            "renderedFrames": max(0, int(self.rendered_frames)),
            "encodedFrames": max(0, int(self.encoded_frames)),
            "frameCount": max(0, int(self.frame_count)),
            "detail": self.detail,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "finishedAt": self.finished_at,
        }


class RenderCancelled(RuntimeError):
    pass


class RenderJobStore:
    """Atomic disk-backed state plus in-process cancellation signals.

    The on-disk JSON remains readable after an API restart. Cancellation signals are
    process-local by design; a cancelled persisted job is never resumed implicitly.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cancel: dict[str, threading.Event] = {}

    @staticmethod
    def validate_id(job_id: str) -> str:
        value = str(job_id or "")
        if not value or len(value) > 100 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in value):
            raise ValueError("Invalid render job id")
        return value

    def path(self, job_id: str) -> Path:
        return self.root / f"{self.validate_id(job_id)}.json"

    def write(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            out = self.path(job_id)
            value = dict(payload)
            value.setdefault("job_id", job_id)
            value.setdefault("updatedAt", time.time())
            value["updatedAt"] = time.time()
            temp = out.with_suffix(".tmp")
            temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(temp, out)
            return value

    def read(self, job_id: str) -> dict[str, Any] | None:
        path = self.path(job_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def create(self, job_id: str, **payload: Any) -> dict[str, Any]:
        event = threading.Event()
        with self._lock:
            self._cancel[job_id] = event
        return self.write(job_id, {"job_id": job_id, "status": "queued", "progress": RenderProgress().to_dict(), **payload})

    def event(self, job_id: str) -> threading.Event:
        with self._lock:
            return self._cancel.setdefault(job_id, threading.Event())

    def cancel(self, job_id: str) -> bool:
        current = self.read(job_id)
        if not current or str(current.get("status")) in TERMINAL_STATES:
            return False
        self.event(job_id).set()
        progress = dict(current.get("progress") or {})
        progress.update({"stage": "cancelled", "detail": "Cancelled by user", "finishedAt": time.time(), "updatedAt": time.time()})
        self.write(job_id, {**current, "status": "cancelled", "progress": progress})
        return True

    def assert_not_cancelled(self, job_id: str) -> None:
        if self.event(job_id).is_set():
            raise RenderCancelled("Render cancelled")
        current = self.read(job_id)
        if current and current.get("status") == "cancelled":
            self.event(job_id).set()
            raise RenderCancelled("Render cancelled")

    def update_progress(
        self,
        job_id: str,
        *,
        stage: str,
        progress: float,
        detail: str = "",
        rendered_frames: int | None = None,
        encoded_frames: int | None = None,
        frame_count: int | None = None,
        status: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        self.assert_not_cancelled(job_id)
        current = self.read(job_id) or {"job_id": job_id}
        existing = dict(current.get("progress") or {})
        started = existing.get("startedAt") or time.time()
        payload = RenderProgress(
            stage=stage,
            progress=progress,
            rendered_frames=int(rendered_frames if rendered_frames is not None else existing.get("renderedFrames") or 0),
            encoded_frames=int(encoded_frames if encoded_frames is not None else existing.get("encodedFrames") or 0),
            frame_count=int(frame_count if frame_count is not None else existing.get("frameCount") or 0),
            detail=detail,
            started_at=float(started),
            updated_at=time.time(),
        ).to_dict()
        return self.write(
            job_id,
            {
                **current,
                **extra,
                "status": status or (stage if stage in ACTIVE_STATES else current.get("status") or "processing"),
                "progress": payload,
            },
        )

    def finish(self, job_id: str, *, result: dict[str, Any] | None = None, status: str = "done", error: str | None = None) -> dict[str, Any]:
        current = self.read(job_id) or {"job_id": job_id}
        now = time.time()
        existing = dict(current.get("progress") or {})
        progress = {
            **existing,
            "stage": status,
            "progress": 1.0 if status == "done" else float(existing.get("progress") or 0),
            "detail": error or existing.get("detail") or "",
            "updatedAt": now,
            "finishedAt": now,
        }
        value = self.write(job_id, {**current, "status": status, "progress": progress, "result": result, "error": error})
        with self._lock:
            self._cancel.pop(job_id, None)
        return value
