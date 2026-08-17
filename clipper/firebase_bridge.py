from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .media import is_social_url
from .pipeline import process_video
from .publish import PREFERRED_RATIO, UploadPostPublisher


class FirebaseBridge:
    """Firebase-backed handoff between the mobile/web uploader and a trusted desktop worker."""

    ACTIVE_STATES = {"claimed", "processing", "uploading", "publishing"}

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
                "lastError": None,
            })
            value = snapshot.to_dict()
            value["id"] = snapshot.id
            return value

        return claim(transaction, reference)

    def claim_next(self) -> dict | None:
        docs = list(self.db.collection("clipperJobs").where("status", "==", "queued").limit(20).stream())
        docs.sort(key=lambda snapshot: str((snapshot.to_dict() or {}).get("createdAt") or ""))
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
            if data.get("workerId") != self.worker_id or data.get("status") not in self.ACTIVE_STATES:
                return
            ref.update({
                "leaseExpiresAt": datetime.now(timezone.utc) + timedelta(minutes=3),
                "updatedAt": self.firestore.SERVER_TIMESTAMP,
            })
            self._worker_state(data.get("status", "processing"), job_id)

    def _requeue_if_expired(self, reference, now: datetime) -> bool:
        """Transactionally re-check a stale lease before returning a job to queue.

        The query that found the document can race a heartbeat. Reading it again
        inside a Firestore transaction prevents an active worker from being
        requeued after it has already renewed its lease.
        """
        transaction = self.db.transaction()
        firestore = self.firestore

        @firestore.transactional
        def requeue(transaction, reference):
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                return False
            data = snapshot.to_dict() or {}
            lease = data.get("leaseExpiresAt")
            if data.get("status") not in self.ACTIVE_STATES or not lease or lease >= now:
                return False
            transaction.update(reference, {
                "status": "queued",
                "workerId": None,
                "leaseExpiresAt": None,
                "updatedAt": firestore.SERVER_TIMESTAMP,
                "lastError": "Worker lease expired; job returned to queue",
            })
            return True

        return bool(requeue(transaction, reference))

    def requeue_expired(self) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        for status in sorted(self.ACTIVE_STATES):
            snapshots = self.db.collection("clipperJobs").where("status", "==", status).limit(50).stream()
            for snapshot in snapshots:
                data = snapshot.to_dict() or {}
                lease = data.get("leaseExpiresAt")
                if lease and lease < now and self._requeue_if_expired(snapshot.reference, now):
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
        suffix = Path(safe_path).suffix or ".bin"
        local = self.settings.workdir / "firebase_inbox" / job["id"] / local_group / f"input_{index}{suffix}"
        local.parent.mkdir(parents=True, exist_ok=True)
        temp = local.with_name(local.name + ".part")
        temp.unlink(missing_ok=True)
        try:
            self.bucket.blob(safe_path).download_to_filename(str(temp), timeout=600)
            if not temp.is_file() or temp.stat().st_size <= 0:
                raise RuntimeError(f"Firebase Storage returned an empty file for {safe_path}")
            os.replace(temp, local)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        return local

    def _source_value(self, job: dict) -> str:
        storage_path = str(job.get("sourceStoragePath") or "")
        if storage_path:
            return str(self._download_blob(job, storage_path, "source"))
        source_url = str(job.get("sourceUrl") or "").strip()
        if source_url:
            if not bool(job.get("ownContentAck", False)):
                raise RuntimeError("Social URL job is missing content ownership/permission acknowledgement")
            if not is_social_url(source_url):
                raise RuntimeError("Firebase remote source is not a supported social URL")
            return source_url
        raise RuntimeError("Firebase job has neither sourceStoragePath nor sourceUrl")

    def _download_extras(self, job: dict) -> tuple[list[str], str | None, str | None, str | None]:
        cameras = []
        for index, storage_path in enumerate(job.get("secondaryStoragePaths") or []):
            cameras.append(str(self._download_blob(job, str(storage_path), "cameras", index)))
        audio_path = job.get("externalAudioStoragePath")
        audio = str(self._download_blob(job, str(audio_path), "external_audio")) if audio_path else None
        music_path = job.get("musicStoragePath")
        music = str(self._download_blob(job, str(music_path), "music")) if music_path else None
        logo_path = job.get("logoStoragePath")
        logo = str(self._download_blob(job, str(logo_path), "brand")) if logo_path else None
        return cameras, audio, music, logo

    def _settings_for_job(self, job: dict, music: str | None, logo: str | None) -> Settings:
        settings = replace(self.settings)
        if "smartCut" in job:
            settings.smart_cut = bool(job.get("smartCut"))
        if "removeFillers" in job:
            settings.remove_fillers = bool(job.get("removeFillers"))
        if "punchIns" in job:
            settings.punch_ins = bool(job.get("punchIns"))
        if "hookOverlay" in job:
            settings.hook_overlay = bool(job.get("hookOverlay"))
        preset = str(job.get("captionPreset") or settings.caption_preset)
        if preset in {"karaoke", "clean", "minimal"}:
            settings.caption_preset = preset
        if music:
            settings.music_path = music

        brand_data = dict(job.get("brand") or {})
        if settings.caption_preset:
            brand_data["caption_preset"] = settings.caption_preset
        if logo:
            brand_data["logo_path"] = logo
        if brand_data:
            brand_path = self.settings.workdir / "firebase_inbox" / job["id"] / "brand" / "brand.json"
            brand_path.parent.mkdir(parents=True, exist_ok=True)
            temp = brand_path.with_suffix(".json.part")
            temp.write_text(json.dumps(brand_data, indent=2), encoding="utf-8")
            os.replace(temp, brand_path)
            settings.brand_kit_path = str(brand_path)
        return settings

    def _upload_outputs(self, job: dict, manifest) -> list[dict]:
        user_id = str(job.get("userId") or "")
        if not user_id:
            raise RuntimeError("Firebase job has no userId")
        job_id = job["id"]
        outputs: list[dict] = []
        for clip in manifest.clips:
            candidate = clip.get("candidate", {})
            metadata = clip.get("social_metadata") or {}
            for variant in clip.get("variants", []):
                local = Path(variant["path"])
                if not local.is_file():
                    continue
                ratio = str(variant.get("aspect_ratio") or "unknown")
                clip_id = str(variant.get("clip_id") or candidate.get("id") or "clip")
                remote_dir = f"users/{user_id}/projects/{job_id}/clips/{clip_id}/{ratio.replace(':', 'x')}"
                remote = f"{remote_dir}/{local.name}"
                self.bucket.blob(remote).upload_from_filename(str(local), content_type="video/mp4", timeout=600)
                thumb_remote = None
                thumbnail_path = variant.get("thumbnail_path")
                if thumbnail_path and Path(thumbnail_path).is_file():
                    thumb = Path(thumbnail_path)
                    thumb_remote = f"{remote_dir}/{thumb.name}"
                    self.bucket.blob(thumb_remote).upload_from_filename(str(thumb), content_type="image/jpeg", timeout=120)
                outputs.append({
                    "clipId": clip_id,
                    "aspectRatio": ratio,
                    "layoutMode": variant.get("layout_mode", "auto"),
                    "storagePath": remote,
                    "thumbnailStoragePath": thumb_remote,
                    "filename": local.name,
                    "width": variant.get("width"),
                    "height": variant.get("height"),
                    "title": metadata.get("title") or candidate.get("title"),
                    "score": candidate.get("score"),
                    "metrics": candidate.get("metrics") or {},
                    "socialMetadata": metadata,
                })
        return outputs

    def _upload_edit_plan(self, job: dict, manifest) -> str | None:
        if not manifest.edit_plan_path or not Path(manifest.edit_plan_path).is_file():
            return None
        user_id = str(job.get("userId") or "")
        remote = f"users/{user_id}/projects/{job['id']}/edit_plan.json"
        self.bucket.blob(remote).upload_from_filename(manifest.edit_plan_path, content_type="application/json", timeout=120)
        return remote

    def _publish_outputs(self, job: dict, manifest) -> tuple[list[dict], list[str]]:
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
        manual_description = str(job.get("publishDescription") or "").strip()
        for clip in manifest.clips:
            candidate = clip.get("candidate", {})
            metadata = clip.get("social_metadata") or {}
            variants = clip.get("variants", [])
            by_ratio: dict[str, list[str]] = {}
            for platform in platforms:
                ratio = PREFERRED_RATIO.get(platform, "9:16")
                by_ratio.setdefault(ratio, []).append(platform)
            for ratio, group in by_ratio.items():
                matches = [
                    variant
                    for variant in variants
                    if variant.get("aspect_ratio") == ratio and Path(variant.get("path", "")).is_file()
                ]
                fallback = [variant for variant in variants if Path(variant.get("path", "")).is_file()]
                variant = (matches or fallback or [None])[0]
                if not variant:
                    errors.append(f"{candidate.get('id', 'clip')}: no rendered file available for {', '.join(group)}")
                    continue
                try:
                    platform_meta = metadata.get("platforms") or {}
                    description = manual_description or metadata.get("caption", "")
                    if len(group) == 1:
                        per_platform = platform_meta.get(group[0]) or {}
                        description = (
                            manual_description
                            or per_platform.get("caption")
                            or per_platform.get("description")
                            or description
                        )
                    response = publisher.upload_video(
                        variant["path"],
                        group,
                        title=str(metadata.get("title") or candidate.get("title") or ""),
                        description=str(description or ""),
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
            cameras, external_audio, music, logo = self._download_extras(job)
            job_settings = self._settings_for_job(job, music, logo)
            manifest = process_video(
                source,
                ratios=list(job.get("ratios") or ["9:16"]),
                own_content_ack=bool(job.get("ownContentAck", False)),
                settings=job_settings,
                secondary_cameras=cameras,
                external_audio=external_audio,
                alternate_visual_layouts=bool(job.get("alternateVisualLayouts", False)),
            )
            ref.update({"status": "uploading", "updatedAt": self.firestore.SERVER_TIMESTAMP})
            self._worker_state("uploading", job_id)
            outputs = self._upload_outputs(job, manifest)
            edit_plan_storage_path = self._upload_edit_plan(job, manifest)

            publish_results: list[dict] = []
            publish_errors: list[str] = []
            if job.get("publishPlatforms"):
                ref.update({"status": "publishing", "updatedAt": self.firestore.SERVER_TIMESTAMP})
                self._worker_state("publishing", job_id)
                publish_results, publish_errors = self._publish_outputs(job, manifest)

            clip_metadata = {
                str((clip.get("candidate") or {}).get("id") or index): {
                    "candidate": clip.get("candidate"),
                    "hookText": clip.get("hook_text"),
                    "socialMetadata": clip.get("social_metadata"),
                    "smartCutIntervals": clip.get("smart_cut_intervals"),
                    "punchIns": clip.get("punch_ins"),
                    "visualCues": clip.get("visual_cues") or [],
                }
                for index, clip in enumerate(manifest.clips)
            }
            project = {
                "userId": job.get("userId"),
                "jobId": job_id,
                "sourceName": job.get("sourceName") or manifest.source_name,
                "sourceStoragePath": job.get("sourceStoragePath"),
                "sourceUrl": job.get("sourceUrl"),
                "ratios": list(job.get("ratios") or ["9:16"]),
                "outputs": outputs,
                "clipMetadata": clip_metadata,
                "editPlanStoragePath": edit_plan_storage_path,
                "hardwareProfile": manifest.hardware_profile,
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
                "status": "done",
                "outputs": outputs,
                "editPlanStoragePath": edit_plan_storage_path,
                "publishResults": publish_results,
                "publishErrors": publish_errors,
                "completedAt": self.firestore.SERVER_TIMESTAMP,
                "updatedAt": self.firestore.SERVER_TIMESTAMP,
            })
        except Exception as exc:
            ref.update({
                "status": "failed",
                "lastError": str(exc)[:4000],
                "updatedAt": self.firestore.SERVER_TIMESTAMP,
            })
            raise
        finally:
            stop.set()
            heartbeat.join(timeout=2)
            shutil.rmtree(self.settings.workdir / "firebase_inbox" / job_id, ignore_errors=True)
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
