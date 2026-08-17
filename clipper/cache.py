from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def file_fingerprint(path: str | Path, *, sample_bytes: int = 1024 * 1024) -> str:
    """Fast, copy-stable content fingerprint for large media files.

    Cache identity must survive Firebase/local ingest copying the same recording
    into a new project, so filesystem mtime is deliberately excluded. Instead we
    hash file size plus sampled content from the beginning, middle, and end. The
    middle sample closes the old blind spot where two same-size files with equal
    first/last megabytes could collide even if their actual video payload differed.
    """
    p = Path(path)
    stat = p.stat()
    size = int(stat.st_size)
    sample = max(64 * 1024, int(sample_bytes))
    h = hashlib.sha256()
    h.update(b"clipper-media-fingerprint-v2\0")
    h.update(str(size).encode())

    offsets = [0]
    if size > sample:
        offsets.extend([
            max(0, (size - sample) // 2),
            max(0, size - sample),
        ])

    with p.open("rb") as handle:
        for offset in sorted(set(offsets)):
            handle.seek(offset)
            chunk = handle.read(sample)
            h.update(str(offset).encode())
            h.update(b"\0")
            h.update(chunk)
    return h.hexdigest()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StageCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, stage: str, key: str) -> Path:
        return self.root / stage / f"{key}.json"

    def load(self, stage: str, key: str) -> dict | None:
        path = self._meta_path(stage, key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        artifacts = data.get("artifacts") or []
        for artifact in artifacts:
            if artifact and not Path(artifact).is_file():
                return None
        return data

    def save(self, stage: str, key: str, payload: dict, artifacts: list[str] | None = None) -> Path:
        path = self._meta_path(stage, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        data = dict(payload)
        data["artifacts"] = list(artifacts or [])
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temp, path)
        return path

    def key_for(self, stage: str, source_path: str | Path, options: dict) -> str:
        return stable_hash({
            "stage": stage,
            "source": file_fingerprint(source_path),
            "options": options,
        })[:32]
