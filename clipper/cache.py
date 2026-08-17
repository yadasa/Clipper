from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def file_fingerprint(path: str | Path, *, sample_bytes: int = 1024 * 1024) -> str:
    """Fast content-aware fingerprint for large media files.

    Hashes size + mtime + first/last sample. It avoids reading multi-GB recordings
    end-to-end while still invalidating when the file materially changes.
    """
    p = Path(path)
    stat = p.stat()
    h = hashlib.sha256()
    h.update(str(stat.st_size).encode())
    h.update(str(stat.st_mtime_ns).encode())
    with p.open("rb") as handle:
        h.update(handle.read(sample_bytes))
        if stat.st_size > sample_bytes:
            handle.seek(max(0, stat.st_size - sample_bytes))
            h.update(handle.read(sample_bytes))
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
