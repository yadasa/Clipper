from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from clipper.config import Settings, normalize_ratios
from clipper.pipeline import list_projects, process_video, rerender_project

settings = Settings()
settings.ensure_dirs()
incoming_dir = settings.workdir / "incoming"
incoming_dir.mkdir(exist_ok=True)
(settings.workdir / "local_jobs").mkdir(exist_ok=True)

app = FastAPI(title="Clipper Local API", version="0.2.0")
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
    path = _job_path(job_id)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def _stage_upload(upload: UploadFile, prefix: str, default_suffix: str = ".bin") -> str:
    suffix = Path(upload.filename or f"upload{default_suffix}").suffix or default_suffix
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


def _build_job_settings(
    *,
    smart_cut: bool,
    remove_fillers: bool,
    punch_ins: bool,
    hook_overlay: bool,
    caption_preset: str,
    music_path: str | None,
    logo_path: str | None,
    brand_font: str,
    brand_accent: str,
    brand_text: str,
    staged_paths: list[str],
) -> Settings:
    job_settings = replace(settings)
    job_settings.smart_cut = smart_cut
    job_settings.remove_fillers = remove_fillers
    job_settings.punch_ins = punch_ins
    job_settings.hook_overlay = hook_overlay
    job_settings.caption_preset = caption_preset if caption_preset in {"karaoke", "clean", "minimal"} else "karaoke"
    job_settings.music_path = music_path or ""

    brand = {
        "name": "local-api",
        "font": (brand_font or "Arial")[:120],
        "accent": brand_accent,
        "primary_text": brand_text,
        "caption_preset": job_settings.caption_preset,
    }
    if logo_path:
        brand["logo_path"] = logo_path
    brand_path = incoming_dir / f"brand-{uuid.uuid4().hex}.json"
    brand_path.write_text(json.dumps(brand, indent=2), encoding="utf-8")
    staged_paths.append(str(brand_path))
    job_settings.brand_kit_path = str(brand_path)
    return job_settings


def _run_local_job(
    job_id: str,
    source: str,
    ratios: list[str],
    own_content_ack: bool,
    secondary_cameras: list[str],
    external_audio: str | None,
    alternate_visual_layouts: bool,
    job_settings: Settings,
    staged_paths: list[str],
) -> None:
    _write_job(job_id, {"job_id": job_id, "status": "processing"})
    try:
        manifest = process_video(
            source,
            ratios=ratios,
            own_content_ack=own_content_ack,
            settings=job_settings,
            secondary_cameras=secondary_cameras,
            external_audio=external_audio,
            alternate_visual_layouts=alternate_visual_layouts,
        )
        _write_job(job_id, {"job_id": job_id, "status": "done", "project": manifest.to_dict()})
    except Exception as exc:
        _write_job(job_id, {"job_id": job_id, "status": "failed", "error": str(exc)})
    finally:
        _cleanup_staged(staged_paths)


def _project_root(project_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", project_id):
        raise HTTPException(400, "Invalid project id")
    root = (settings.workdir / "projects" / project_id).resolve()
    projects_root = (settings.workdir / "projects").resolve()
    if root.parent != projects_root or not (root / "manifest.json").is_file():
        raise HTTPException(404, "Project not found")
    return root


def _run_rerender_job(job_id: str, project_root: Path) -> None:
    _write_job(job_id, {"job_id": job_id, "status": "processing", "project_id": project_root.name})
    try:
        manifest = rerender_project(project_root, settings=replace(settings))
        _write_job(job_id, {"job_id": job_id, "status": "done", "project": manifest.to_dict()})
    except Exception as exc:
        _write_job(job_id, {"job_id": job_id, "status": "failed", "error": str(exc)})


@app.get("/api/health")
def health():
    profile = settings.apply_hardware_profile()
    return {
        "ok": True,
        "mode": "local",
        "workdir": str(settings.workdir),
        "hardware": profile.to_dict() if profile else None,
    }


@app.get("/api/projects")
def projects():
    return list_projects(settings)


@app.get("/api/jobs/{job_id}")
def local_job(job_id: str):
    path = _job_path(job_id)
    if not path.is_file():
        raise HTTPException(404, "Job not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/projects/{project_id}/rerender", status_code=202)
def rerender(project_id: str, background_tasks: BackgroundTasks):
    root = _project_root(project_id)
    job_id = uuid.uuid4().hex[:16]
    _write_job(job_id, {"job_id": job_id, "status": "queued", "project_id": project_id})
    background_tasks.add_task(_run_rerender_job, job_id, root)
    return {"job_id": job_id, "status": "queued", "project_id": project_id}


@app.post("/api/process", status_code=202)
def process(
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None),
    secondary_files: list[UploadFile] | None = File(default=None),
    external_audio: UploadFile | None = File(default=None),
    music: UploadFile | None = File(default=None),
    logo: UploadFile | None = File(default=None),
    source_url: str = Form(default=""),
    own_content_ack: bool = Form(default=False),
    ratios: str = Form(default="9:16"),
    alternate_visual_layouts: bool = Form(default=False),
    smart_cut: bool = Form(default=True),
    remove_fillers: bool = Form(default=True),
    punch_ins: bool = Form(default=True),
    hook_overlay: bool = Form(default=True),
    caption_preset: str = Form(default="karaoke"),
    brand_font: str = Form(default="Arial"),
    brand_accent: str = Form(default="#D6A77A"),
    brand_text: str = Form(default="#FFFFFF"),
):
    requested = normalize_ratios([x.strip() for x in ratios.split(",") if x.strip()])
    if file is None and not source_url.strip():
        raise HTTPException(400, "Upload a file or provide a source URL")
    if file is not None and source_url.strip():
        raise HTTPException(400, "Choose either file upload or source URL, not both")
    if caption_preset not in {"karaoke", "clean", "minimal"}:
        raise HTTPException(400, "caption_preset must be karaoke, clean, or minimal")

    staged_paths: list[str] = []
    source = source_url.strip()
    if file is not None:
        source = _stage_upload(file, "primary", ".mp4")
        staged_paths.append(source)

    cameras: list[str] = []
    for upload in secondary_files or []:
        path = _stage_upload(upload, "camera", ".mp4")
        cameras.append(path)
        staged_paths.append(path)

    audio_path = None
    if external_audio is not None:
        audio_path = _stage_upload(external_audio, "mic", ".wav")
        staged_paths.append(audio_path)

    music_path = None
    if music is not None:
        music_path = _stage_upload(music, "music", ".mp3")
        staged_paths.append(music_path)

    logo_path = None
    if logo is not None:
        logo_path = _stage_upload(logo, "logo", ".png")
        staged_paths.append(logo_path)

    job_settings = _build_job_settings(
        smart_cut=smart_cut,
        remove_fillers=remove_fillers,
        punch_ins=punch_ins,
        hook_overlay=hook_overlay,
        caption_preset=caption_preset,
        music_path=music_path,
        logo_path=logo_path,
        brand_font=brand_font,
        brand_accent=brand_accent,
        brand_text=brand_text,
        staged_paths=staged_paths,
    )

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
        job_settings,
        staged_paths,
    )
    return {"job_id": job_id, "status": "queued"}
