from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .pipeline import process_video
from .publish import PREFERRED_RATIO, UploadPostPublisher


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
        self.worker_ref = self.db.collection("clipperWorkers").document(self.worker_id)
        self.settings.ensure_dirs()
        self._worker_state("idle")

    def _worker_state(self, state: str, job_id: str | None = None) -> None:
        self.worker_ref.set({
            "workerId": self.worker_id,
            "state": state,
            "currentJobId": job_id,
            "lastSeenAt": self.firestore.SERVER_TIMESTAMP,
        }, merge=True)

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
        docs = list(self.db.collection("clipperJobs").where("status", "==", "queued").limit(20).stream())
        docs.sort(key=lambda s: str((s.to_dict() or {}).get("createdAt") or ""))
        for snapshot in docs:
            job = self._claim(snapshot.reference)
            if job:
                return job
        return None

    def _heartbeat(self, job_id: str, stop: threading.Event) -> None:
        ref = self.db.collection("clipperJobs").document(job_id)
        active_states = {"claimed", "processing", "uploading", "publishing"}
        while not stop.wait(45):
            snapshot = ref.get()
            data = snapshot.to_dict() or {}
            if data.get("workerId") != self.worker_id or data.get("status") not in active_states:
                return
            ref.update({
                "leaseExpiresAt": datetime.now(timezone.utc) + timedelta(minutes=3),
                "updatedAt": self.firestore.SERVER_TIMESTAMP,
            })
            self._worker_state(data.get("status", "processing"), job_id)

    def requeue_expired(self) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        for status in ("claimed", "processing", "uploading", "publishing"):
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

    def _expected_source_prefix(self, job: dict) -> str:
        user_id = str(job.get("userId") or "").strip()
        job_id = str(job.get("id") or "").strip()
        if not user_id or not job_id:
            raise RuntimeError("Firebase job is missing userId or id")
        return f"users/{user_id}/sources/{job_id}/"

    def _validated_storage_path(self, job: dict, storage_path: str) -> str:
        path = str(storage_path or "").strip().lstrip("/")
        prefix = self._expected_source_prefix(job)
        if not path.startswith(prefix):
            raise RuntimeError("Refusing Firebase media path outside this user's job source folder")
        if ".." in Path(path).parts:
            raise RuntimeError("Refusing unsafe Firebase media path")
        return path

    def _download_blob(self, job: dict, storage_path: str, local_group: str, index: int = 0) -> Path:
        safe_path = self._validated_storage_path(job, storage_path)
        suffix = Path(safe_path).suffix or ".mp4"
        local = self.settings.workdir / "firebase_inbox" / job["id"] / local_group / f"input_{index}{suffix}"
        local.parent.mkdir(parents=True, exist_ok=True)
        self.bucket.blob(safe_path).download_to_filename(str(local))
        return local

    def _source_value(self, job: dict) -> str:
        storage_path = str(job.get("sourceStoragePath") or "")
        if storage_path:
            return str(self._download_blob(job, storage_path, "source"))
        source_url = str(job.get("sourceUrl") or "").strip()
        if source_url:
            if not bool(job.get("ownContentAck", False)):
                raise RuntimeError("Social URL job is missing content ownership/permission acknowledgement")
            return source_url
        raise RuntimeError("Firebase job has neither sourceStoragePath nor sourceUrl")

    def _download_extras(self, job: dict) -> tuple[list[str], str | None]:
        cameras = []
        for index, storage_path in enumerate(job.get("secondaryStoragePaths") or []):
            cameras.append(str(self._download_blob(job, str(storage_path), "cameras", index)))
        audio_path = job.get("externalAudioStoragePath")
        audio = str(self._download_blob(job, str(audio_path), "external_audio")) if audio_path else None
        return cameras, audio

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
                if not local.is_file():
                    continue
                ratio = str(variant.get("aspect_ratio") or "unknown")
                clip_id = str(variant.get("clip_id") or candidate.get("id") or "clip")
                remote = f"users/{user_id}/projects/{job_id}/clips/{clip_id}/{ratio.replace(':', 'x')}/{local.name}"
                self.bucket.blob(remote).upload_from_filename(str(local), content_type="video/mp4", timeout=600)
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

    def _publish_outputs(self, job: dict, manifest) -> tuple[list[dict], list[str]]:
        """Publish only when the user explicitly selected platforms for this job.

        Publishing failure never destroys a completed edit. Results and errors are
        returned separately and stored with the project for review/retry.
        """
        platforms = []
        for value in job.get("publishPlatforms") or []:
            name = str(value).strip().lower()
            if name and name not in platforms:
                platforms.append(name)
        if not platforms:
            return [], []

        try:
            publisher = UploadPostPublisher(self.settings)
        except Exception as exc:
            return [], [str(exc)]

        results: list[dict] = []
        errors: list[str] = []
        description = str(job.get("publishDescription") or "")
        for clip in manifest.clips:
            candidate = clip.get("candidate", {})
            variants = clip.get("variants", [])
            by_ratio: dict[str, list[str]] = {}
            for platform in platforms:
                ratio = PREFERRED_RATIO.get(platform, "9:16")
                by_ratio.setdefault(ratio, []).append(platform)
            for ratio, group in by_ratio.items():
                matches = [v for v in variants if v.get("aspect_ratio") == ratio and Path(v.get("path", "")).is_file()]
                fallback = [v for v in variants if Path(v.get("path", "")).is_file()]
                variant = (matches or fallback or [None])[0]
                if not variant:
                    errors.append(f"{candidate.get('id', 'clip')}: no rendered file available for {', '.join(group)}")
                    continue
                try:
                    response = publisher.upload_video(
                        variant["path"], group,
                        title=str(candidate.get("title") or ""),
                        description=description,
                        add_to_queue=bool(job.get("publishQueue", False)),
                    )
                    results.append({
                        "clipId": candidate.get("id"),
                        "platforms": group,
                        "aspectRatio": variant.get("aspect_ratio"),
                        "result": response,
                    })
                except Exception as exc:
                    errors.append(f"{candidate.get('id', 'clip')} -> {', '.join(group)}: {exc}")
        return results, errors

    def process_job(self, job: dict) -> None:
        job_id = job["id"]
        ref = self.db.collection("clipperJobs").document(job_id)
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job_id, stop), daemon=True)
        heartbeat.start()
        self._worker_state("processing", job_id)
        try:
            ref.update({"status": "processing", "updatedAt": self.firestore.SERVER_TIMESTAMP})
            source = self._source_value(job)
            cameras, external_audio = self._download_extras(job)
            manifest = process_video(
                source,
                ratios=list(job.get("ratios") or ["9:16"]),
                own_content_ack=bool(job.get("ownContentAck", False)),
                settings=self.settings,
                secondary_cameras=cameras,
                external_audio=external_audio,
                alternate_visual_layouts=bool(job.get("alternateVisualLayouts", False)),
            )
            ref.update({"status": "uploading", "updatedAt": self.firestore.SERVER_TIMESTAMP})
            self._worker_state("uploading", job_id)
            outputs = self._upload_outputs(job, manifest)

            publish_results: list[dict] = []
            publish_errors: list[str] = []
            if job.get("publishPlatforms"):
                ref.update({"status": "publishing", "updatedAt": self.firestore.SERVER_TIMESTAMP})
                self._worker_state("publishing", job_id)
                publish_results, publish_errors = self._publish_outputs(job, manifest)

            project = {
                "userId": job.get("userId"),
                "jobId": job_id,
                "sourceName": job.get("sourceName") or manifest.source_name,
                "sourceStoragePath": job.get("sourceStoragePath"),
                "sourceUrl": job.get("sourceUrl"),
                "ratios": list(job.get("ratios") or ["9:16"]),
                "outputs": outputs,
                "clipCount": len(manifest.clips),
                "status": "done",
                "publishPlatforms": list(job.get("publishPlatforms") or []),
                "publishResults": publish_results,
                "publishErrors": publish_errors,
                "createdAt": job.get("createdAt"),
                "completedAt": self.firestore.SERVER_TIMESTAMP,
            }
            self.db.collection("clipperProjects").document(job_id).set(project)
            ref.update({
                "status": "done", "outputs": outputs,
                "publishResults": publish_results,
                "publishErrors": publish_errors,
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
            self._worker_state("idle")

    def run_forever(self) -> None:
        while True:
            try:
                self.requeue_expired()
                job = self.claim_next()
                if job:
                    self.process_job(job)
                    continue
                self._worker_state("idle")
            except KeyboardInterrupt:
                self._worker_state("offline")
                raise
            except Exception as exc:
                self._worker_state("error")
                print(f"[firebase-worker] {type(exc).__name__}: {exc}")
            time.sleep(max(2, self.settings.firebase_poll_seconds))
