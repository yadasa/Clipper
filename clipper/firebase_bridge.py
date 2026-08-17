from __future__ import annotations

import json
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .pipeline import process_video


class FirebaseBridge:
    """Firebase-backed handoff between the mobile/web uploader and a trusted desktop worker."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        if not self.settings.firebase_storage_bucket:
            raise RuntimeError("FIREBASE_STORAGE_BUCKET is required")
        try:
            import firebase_admin
            from firebase_admin import firestore, storage
        except ImportError as exc:
            raise RuntimeError("firebase-admin is required for the Firebase bridge") from exc

        options = {"storageBucket": self.settings.firebase_storage_bucket}
        if self.settings.firebase_project_id:
            options["projectId"] = self.settings.firebase_project_id
        try:
            self.app = firebase_admin.get_app()
        except ValueError:
            self.app = firebase_admin.initialize_app(options=options)
        self.firestore = firestore
        self.db = firestore.client(self.app)
        self.bucket = storage.bucket(app=self.app)
        self.worker_id = self.settings.firebase_worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.settings.ensure_dirs()

    def _claim(self, reference):
        transaction = self.db.transaction()
        firestore = self.firestore

        @firestore.transactional
        def claim(transaction, reference):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists or snapshot.get("status") != "queued":
                return None
            now = datetime.now(timezone.utc)
            transaction.update(reference, {
                "status": "claimed",
                "workerId": self.worker_id,
                "claimedAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP,
                "leaseExpiresAt": now + timedelta(minutes=3),
            })
            value = snapshot.to_dict()
            value["id"] = snapshot.id
            return value

        return claim(transaction, reference)

    def claim_next(self) -> dict | None:
        # A single-field status query avoids requiring a custom composite index.
        docs = list(self.db.collection("clipperJobs").where("status", "==", "queued").limit(20).stream())
        docs.sort(key=lambda s: str((s.to_dict() or {}).get("createdAt") or ""))
        for snapshot in docs:
            job = self._claim(snapshot.reference)
            if job:
                return job
        return None

    def _heartbeat(self, job_id: str, stop: threading.Event) -> None:
        ref = self.db.collection("clipperJobs").document(job_id)
        while not stop.wait(45):
            snapshot = ref.get()
            data = snapshot.to_dict() or {}
            if data.get("workerId") != self.worker_id or data.get("status") not in {"claimed", "processing", "uploading"}:
                return
            ref.update({
                "leaseExpiresAt": datetime.now(timezone.utc) + timedelta(minutes=3),
                "updatedAt": self.firestore.SERVER_TIMESTAMP,
            })

    def requeue_expired(self) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        for status in ("claimed", "processing", "uploading"):
            for snapshot in self.db.collection("clipperJobs").where("status", "==", status).limit(50).stream():
                data = snapshot.to_dict() or {}
                lease = data.get("leaseExpiresAt")
                if lease and lease < now:
                    snapshot.reference.update({
                        "status": "queued", "workerId": None,
                        "updatedAt": self.firestore.SERVER_TIMESTAMP,
                        "lastError": "Worker lease expired; job returned to queue",
                    })
                    count += 1
        return count

    def _download_source(self, job: dict) -> Path:
        job_id = job["id"]
        storage_path = str(job.get("sourceStoragePath") or "")
        if not storage_path:
            raise RuntimeError("Firebase job has no sourceStoragePath")
        suffix = Path(storage_path).suffix or ".mp4"
        local = self.settings.workdir / "firebase_inbox" / job_id / f"source{suffix}"
        local.parent.mkdir(parents=True, exist_ok=True)
        self.bucket.blob(storage_path).download_to_filename(str(local))
        return local

    def _upload_outputs(self, job: dict, manifest) -> list[dict]:
        user_id = str(job.get("userId") or "")
        if not user_id:
            raise RuntimeError("Firebase job has no userId")
        job_id = job["id"]
        outputs: list[dict] = []
        for clip in manifest.clips:
            candidate = clip.get("candidate", {})
            for variant in clip.get("variants", []):
                local = Path(variant["path"])
                ratio = str(variant.get("aspect_ratio") or "unknown")
                clip_id = str(variant.get("clip_id") or candidate.get("id") or "clip")
                remote = f"users/{user_id}/projects/{job_id}/clips/{clip_id}/{ratio.replace(':', 'x')}/{local.name}"
                blob = self.bucket.blob(remote)
                blob.upload_from_filename(str(local), content_type="video/mp4", timeout=600)
                outputs.append({
                    "clipId": clip_id,
                    "aspectRatio": ratio,
                    "storagePath": remote,
                    "filename": local.name,
                    "width": variant.get("width"),
                    "height": variant.get("height"),
                    "title": candidate.get("title"),
                    "score": candidate.get("score"),
                })
        return outputs

    def process_job(self, job: dict) -> None:
        job_id = job["id"]
        ref = self.db.collection("clipperJobs").document(job_id)
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job_id, stop), daemon=True)
        heartbeat.start()
        try:
            ref.update({"status": "processing", "updatedAt": self.firestore.SERVER_TIMESTAMP})
            source = self._download_source(job)
            manifest = process_video(
                str(source),
                ratios=list(job.get("ratios") or ["9:16"]),
                own_content_ack=bool(job.get("ownContentAck", False)),
                settings=self.settings,
                alternate_visual_layouts=bool(job.get("alternateVisualLayouts", True)),
            )
            ref.update({"status": "uploading", "updatedAt": self.firestore.SERVER_TIMESTAMP})
            outputs = self._upload_outputs(job, manifest)
            project = {
                "userId": job.get("userId"),
                "jobId": job_id,
                "sourceName": job.get("sourceName") or manifest.source_name,
                "sourceStoragePath": job.get("sourceStoragePath"),
                "ratios": list(job.get("ratios") or ["9:16"]),
                "outputs": outputs,
                "clipCount": len(manifest.clips),
                "status": "done",
                "createdAt": job.get("createdAt"),
                "completedAt": self.firestore.SERVER_TIMESTAMP,
            }
            self.db.collection("clipperProjects").document(job_id).set(project)
            ref.update({
                "status": "done", "outputs": outputs,
                "completedAt": self.firestore.SERVER_TIMESTAMP,
                "updatedAt": self.firestore.SERVER_TIMESTAMP,
            })
        except Exception as exc:
            ref.update({
                "status": "failed", "lastError": str(exc)[:4000],
                "updatedAt": self.firestore.SERVER_TIMESTAMP,
            })
            raise
        finally:
            stop.set()
            heartbeat.join(timeout=2)

    def run_forever(self) -> None:
        while True:
            try:
                self.requeue_expired()
                job = self.claim_next()
                if job:
                    self.process_job(job)
                    continue
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[firebase-worker] {type(exc).__name__}: {exc}")
            time.sleep(max(2, self.settings.firebase_poll_seconds))
