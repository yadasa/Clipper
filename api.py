from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from clipper.config import Settings, normalize_ratios
from clipper.pipeline import list_projects, process_video

settings = Settings()
settings.ensure_dirs()
incoming_dir = settings.workdir / "incoming"
incoming_dir.mkdir(exist_ok=True)
(settings.workdir / "local_jobs").mkdir(exist_ok=True)

app = FastAPI(title="Clipper Local API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5175"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/media", StaticFiles(directory=str(settings.workdir)), name="media")


def _job_path(job_id: str) -> Path:
    return settings.workdir / "local_jobs" / f"{job_id}.json"


def _write_job(job_id: str, payload: dict) -> None:
    _job_path(job_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _stage_upload(upload: UploadFile, prefix: str) -> str:
    suffix = Path(upload.filename or "upload.mp4").suffix or ".mp4"
    staging = incoming_dir / f"{prefix}-{uuid.uuid4().hex}{suffix}"
    with staging.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    return str(staging)


def _cleanup_staged(paths: list[str]) -> None:
    root = incoming_dir.resolve()
    for value in paths:
        try:
            path = Path(value).resolve()
            if path.parent == root and path.is_file():
                path.unlink()
        except Exception:
            pass


def _run_local_job(
    job_id: str,
    source: str,
    ratios: list[str],
    own_content_ack: bool,
    secondary_cameras: list[str],
    external_audio: str | None,
    alternate_visual_layouts: bool,
    staged_paths: list[str],
) -> None:
    _write_job(job_id, {"job_id": job_id, "status": "processing"})
    try:
        manifest = process_video(
            source,
            ratios=ratios,
            own_content_ack=own_content_ack,
            settings=settings,
            secondary_cameras=secondary_cameras,
            external_audio=external_audio,
            alternate_visual_layouts=alternate_visual_layouts,
        )
        _write_job(job_id, {"job_id": job_id, "status": "done", "project": manifest.to_dict()})
    except Exception as exc:
        _write_job(job_id, {"job_id": job_id, "status": "failed", "error": str(exc)})
    finally:
        _cleanup_staged(staged_paths)


@app.get("/api/health")
def health():
    return {"ok": True, "mode": "local", "workdir": str(settings.workdir)}


@app.get("/api/projects")
def projects():
    return list_projects(settings)


@app.get("/api/jobs/{job_id}")
def local_job(job_id: str):
    path = _job_path(job_id)
    if not path.is_file():
        raise HTTPException(404, "Job not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/process", status_code=202)
def process(
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None),
    secondary_files: list[UploadFile] | None = File(default=None),
    external_audio: UploadFile | None = File(default=None),
    source_url: str = Form(default=""),
    own_content_ack: bool = Form(default=False),
    ratios: str = Form(default="9:16"),
    alternate_visual_layouts: bool = Form(default=False),
):
    requested = normalize_ratios([x.strip() for x in ratios.split(",") if x.strip()])
    if file is None and not source_url.strip():
        raise HTTPException(400, "Upload a file or provide a source URL")
    if file is not None and source_url.strip():
        raise HTTPException(400, "Choose either file upload or source URL, not both")

    staged_paths: list[str] = []
    source = source_url.strip()
    if file is not None:
        source = _stage_upload(file, "primary")
        staged_paths.append(source)

    cameras: list[str] = []
    for upload in secondary_files or []:
        path = _stage_upload(upload, "camera")
        cameras.append(path)
        staged_paths.append(path)

    audio_path = None
    if external_audio is not None:
        audio_path = _stage_upload(external_audio, "mic")
        staged_paths.append(audio_path)

    job_id = uuid.uuid4().hex[:16]
    _write_job(job_id, {"job_id": job_id, "status": "queued"})
    background_tasks.add_task(
        _run_local_job,
        job_id,
        source,
        requested,
        own_content_ack,
        cameras,
        audio_path,
        alternate_visual_layouts,
        staged_paths,
    )
    return {"job_id": job_id, "status": "queued"}
