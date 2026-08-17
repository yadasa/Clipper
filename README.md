# Clipper

Local-first automated video clipping for recordings you make, with an optional Firebase handoff when you are away from your desktop.

Clipper takes a local recording or an authorized social-media source, transcribes it with word timestamps, selects strong standalone moments, reframes the speaker for each target aspect ratio, burns captions, finds or generates transcript-matched visuals, and returns ready-to-review clips. Separate microphone tracks and extra cameras can be aligned automatically. A Firebase-hosted uploader can queue work for a home desktop and show the finished versions after the desktop uploads them back.

## What is implemented

- Local file ingest and authorized Instagram/TikTok/YouTube/Facebook/X link ingest through `yt-dlp`.
- faster-whisper transcription with word timestamps, VAD, cached model loading, CUDA batching when available, and CPU int8 fallback.
- Local clip ranking plus optional Gemini JSON reranking.
- Word-boundary clip snapping so cuts avoid chopping spoken words.
- Subject-aware reframing: low-resolution sampled MediaPipe face analysis produces a smoothed crop trajectory; FFmpeg performs the full-resolution dynamic crop natively.
- 9:16, 4:5, 1:1, and 16:9 delivery variants at social-friendly 1080-class dimensions.
- Burned ASS captions generated from word timings.
- Transcript-aware visual planning with Wikimedia Commons retrieval by default and optional local Diffusers image generation.
- Automatic visual composition plus optional alternate split-screen, picture-in-picture, and full-screen/interruption edits.
- Separate-microphone and multicamera synchronization using transcript n-gram anchors, robust clock-drift fitting, and waveform cross-correlation fallback.
- Automatic speech-boundary multicam cuts.
- H.264/AAC MP4 output, `yuv420p`, loudness normalization, metadata scrubbing, and `+faststart` for web/social delivery.
- CPU x264 or NVIDIA NVENC output with automatic fallback.
- Conservative parallel rendering with `RENDER_WORKERS`.
- Local FastAPI processing API.
- Firebase Hosting uploader/library, Firebase Auth, Storage, Firestore queue, desktop worker leases/heartbeats, stale-job recovery, grouped source folders, playback, and download.
- Optional automatic social publishing through Upload-Post. Publishing is opt-in per queued job and publishing failure does not discard a completed edit.
- Beige/brown responsive interface with muted accents.

## Why this is not a byte-for-byte OpenShorts mirror

Clipper was bootstrapped from the public `mutonby/openshorts` project. OpenShorts' root code is MIT-licensed, but its `cloud/` directory has a separate commercial license that prohibits redistribution in another product. Because this repository is public, Clipper does **not** copy that `cloud/` source. The Firebase queue/worker/library in this repository is an independent implementation. Large upstream demo media was also omitted because it is not runtime code.

The MIT-licensed `ffmpeg_utils.py` and `clip_selection.py` helpers were imported and adapted as baseline primitives. See `UPSTREAM_IMPORT.md` and `ARCHITECTURE_REPORT.md` for the full audit.

## Local setup

Requirements:

- Python 3.11 recommended.
- FFmpeg and FFprobe available on `PATH`.
- 8 GB+ RAM recommended.
- NVIDIA GPU optional but useful for faster Whisper, NVENC, and local image generation.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

For optional local Diffusers image generation:

```bash
pip install -r requirements-ai.txt
```

### Process a local recording

```bash
python -m clipper.cli process "C:\Videos\recording.mp4" --ratio 9:16 --ratio 16:9
```

Extra camera and separate microphone:

```bash
python -m clipper.cli process primary.mp4 --camera camera2.mp4 --camera camera3.mp4 --mic recorder.wav --ratio 9:16
```

To also render every split/PIP/interruption alternative, use the web UI option. Automatic mode is the default because rendering four compositions for every clip and ratio can multiply encode time substantially.

## Importing your own social post

```bash
python -m clipper.cli process "https://www.instagram.com/reel/..." --own-content --ratio 9:16
```

Social-link import requires the ownership/permission acknowledgement. Clipper asks `yt-dlp` for the highest-quality source exposed by the platform. If the platform makes an original/cleaner source available to your logged-in account, configure either `YTDLP_COOKIES_FILE` or `YTDLP_COOKIES_FROM_BROWSER` so the home machine can access it.

Clipper does not erase a baked-in watermark from another creator's media. The intended flow is to recover the clean source of media you own.

## Visual/B-roll behavior

Default:

```env
VISUAL_PROVIDER=commons
```

This searches Wikimedia Commons using phrases derived from the transcript and stores attribution metadata beside each retrieved image.

For fully local generated imagery:

```env
VISUAL_PROVIDER=diffusers
DIFFUSION_MODEL=<a compatible Diffusers model id or local path>
```

For Commons first with local generation as fallback:

```env
VISUAL_PROVIDER=auto
DIFFUSION_MODEL=<model>
```

Gemini is optional. Without it, local heuristics select clips and create visual queries. With `GEMINI_API_KEY`, Gemini can rerank candidate clips and produce more contextual visual plans.

## Encoder/performance controls

```env
FFMPEG_ENCODER=auto   # x264, nvenc, or auto
RENDER_WORKERS=2      # independent output renders in parallel
WHISPER_DEVICE=auto   # auto, cuda, cpu
WHISPER_BATCH_SIZE=8
```

`auto` probes NVENC once and safely falls back to libx264. Subject tracking and local image pipelines are cached so alternate ratios/layouts do not repeat expensive model initialization unnecessarily.

## Local API

```bash
uvicorn api:app --host 0.0.0.0 --port 5175
```

Endpoints:

- `GET /api/health`
- `GET /api/projects`
- `GET /api/jobs/{job_id}`
- `POST /api/process`

The CLI is the most complete local interface for multicam/microphone work; the Firebase web uploader also supports those attachments.

## Firebase mobile -> home desktop flow

1. Create/select a Firebase project.
2. Enable Google Authentication, Firestore, Storage, and Hosting.
3. Install the Firebase CLI and deploy this repository's Hosting and rules:

```bash
firebase login
firebase use <your-project-id>
firebase deploy --only hosting,firestore:rules,storage
```

4. On the home desktop, configure:

```env
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_STORAGE_BUCKET=your-bucket
GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\service-account.json
```

5. Start the worker:

```bash
python -m clipper.worker
```

From the hosted site you can then upload a video from a phone, attach extra camera/mic files, or paste an authorized social link. Firestore receives the job, the home worker claims it transactionally, keeps a lease alive while editing, uploads completed files back to Storage, and writes a grouped project record for the browser library.

The worker validates that Storage source paths belong to the requesting user's exact job folder before the Admin SDK reads them.

## Automatic publishing

Configure the home desktop only:

```env
UPLOAD_POST_API_KEY=
UPLOAD_POST_USER=
```

Then select platforms in the web uploader. Publishing remains opt-in. Clipper chooses the closest rendered aspect ratio per platform and uses an idempotency key to avoid accidental duplicate submissions. The original finished files are preserved even if a publishing request fails.

CLI publishing is also available:

```bash
python -m clipper.cli publish data/projects/<project>/manifest.json --platform tiktok --platform instagram
```

## Output organization

Each local source creates:

```text
data/projects/<project-id>/
  manifest.json
  transcript.json
  clip_candidates.json
  source/
  sync/
  visuals/<clip-id>/timeline.json
  clips/<clip-id>/<ratio>/...mp4
```

The Firebase library mirrors the same concept: one project per starting source, with every finished clip and selected aspect-ratio/composition version grouped beneath it.

## Validation

`.github/workflows/ci.yml` compiles every Python source, runs the lightweight regression tests, and parses `web/app.js` as an ES module on every push. The tests cover clip-window behavior, word-edge snapping, audio-sync sign/drift math, and dynamic crop command geometry.

CI validates syntax and pure logic. A real end-to-end render still depends on your machine's FFmpeg build, downloaded Whisper/model weights, optional GPU drivers, Firebase credentials, and a real media file. Use the smoke-test checklist in `IMPLEMENTATION_REPORT.md` when wiring those environment-specific pieces.

## Reports

- `ARCHITECTURE_REPORT.md` — how OpenShorts works, capabilities, speed, gaps, and the Clipper redesign.
- `UPSTREAM_IMPORT.md` — what was imported, what was intentionally excluded, and why.
- `IMPLEMENTATION_REPORT.md` — the ordered request checklist, implementation status, bug/optimization passes, and environment-dependent validation items.

## License

The imported OpenShorts core helpers retain the upstream MIT license notice in the repository root. No OpenShorts `cloud/` commercial source is redistributed here.
