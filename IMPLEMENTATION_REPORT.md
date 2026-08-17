# Clipper implementation report

This file tracks the requested work in the order it was requested. A requested implementation item is checked when the code is present on `main` and its syntax/pure-logic validation passes. Environment-dependent smoke tests are separated at the bottom so they are not falsely represented as executable from GitHub CI.

## Ordered checklist

- [x] Inspect `mutonby/openshorts`, its architecture, capabilities, performance claims, dependencies, and licensing.
- [x] Import the redistributable OpenShorts baseline into `yadasa/Clipper` while preserving upstream attribution and licensing.
- [x] Document how the imported baseline works, its capabilities, speed characteristics, and improvement opportunities.
- [x] Identify what is missing for a true local-first automated clipping workflow for recorded videos and multi-platform publishing.
- [x] Implement the missing local-first automated clipping features.
- [x] After each feature, run focused bug/optimization passes and apply fixes.
- [x] Implement transcript-aware visual/B-roll planning that finds or generates imagery aligned with what is being discussed at each moment.
- [x] Implement multiple visual edit choices: split screen, picture-in-picture, and interrupt/full-screen inserts.
- [x] Optimize visual compositions independently for selected aspect ratios.
- [x] Apply a beige/brown UI theme with muted accent colors.
- [x] Run another full optimization and bug-fix pass.
- [x] Optimize upload for local-device media and pasted Instagram/TikTok links.
- [x] For user-owned social content, prefer the highest-quality clean source exposed by the platform/extractor and keep an explicit ownership acknowledgement in the workflow.
- [x] Add separate audio/video synchronization using timestamped speech, waveform correlation, and drift correction for multicam/separate-microphone recordings.
- [x] Add a mobile/web ingest flow backed by Firebase Storage + Firestore.
- [x] Add a local desktop worker that claims Firebase jobs, downloads source media, edits locally, and uploads results.
- [x] Add a Firebase-hostable library UI organized by source video, with every finished version grouped under that source.
- [x] Store and expose multiple selected aspect-ratio variants per finished clip.
- [x] Allow finished videos to be watched and downloaded from the web UI.
- [x] Add social-media-oriented export presets.
- [x] Add publishing adapter hooks for supported social platforms/services without hard-coding credentials.
- [x] Run a final bug pass.
- [x] Search for and implement additional optimization opportunities.
- [x] Re-audit this checklist against the repository and fix anything missing before declaring the implementation pass complete.

## Licensing/import result

The upstream root license is MIT except for `cloud/`, which is under the OpenShorts Commercial License. The target repository is public, so `cloud/` is not copied into it. That commercial license specifically prohibits redistribution of the `cloud/` software as part of another product without a separate agreement.

Clipper therefore:

- Preserves the upstream root MIT notice.
- Directly imports/adapts the useful MIT `ffmpeg_utils.py` and `clip_selection.py` primitives.
- Preserves an upstream dependency snapshot in `upstream-requirements.txt`.
- Independently implements the Firebase/mobile/desktop workflow instead of redistributing OpenShorts `cloud/` code.
- Omits upstream demo media and unrelated AI UGC/SEO/billing product surfaces.

See `UPSTREAM_IMPORT.md` for the import map and `ARCHITECTURE_REPORT.md` for the upstream breakdown.

## What is implemented now

### Local automated editing

- Local file ingest.
- Authorized social URL ingest through `yt-dlp`.
- faster-whisper transcription with word timestamps and VAD.
- CUDA batched transcription when available; CPU int8 fallback.
- Local clip-ranking heuristic that works without a paid AI API.
- Optional Gemini reranking and visual planning.
- Word-edge boundary snapping.
- 9:16, 4:5, 1:1, and 16:9 outputs.
- Dynamic subject-aware reframe for narrow formats.
- ASS subtitles burned into output.
- H.264/AAC MP4, 1080-class dimensions, `yuv420p`, loudness normalization, metadata scrubbing, and `+faststart`.
- x264/NVENC selection with fallback.

### Transcript-aware imagery and visual edits

- Semantic visual cue windows tied to the words being spoken.
- Wikimedia Commons image retrieval by default.
- Attribution metadata stored beside retrieved assets.
- Optional local Diffusers image generation.
- Automatic aspect-aware visual layout.
- Explicit split-screen alternative.
- Explicit picture-in-picture alternative.
- Explicit full-screen/interruption alternative.
- Optional rendering of every layout version per selected ratio.

### Separate mic and multicam

- Separate microphone transcription.
- Shared transcript n-gram time anchors.
- Robust linear timing fit for absolute offset and recorder clock drift.
- Waveform FFT correlation fallback when transcript anchors are insufficient.
- FFmpeg audio correction for offset/drift.
- Extra camera synchronization against the primary timeline.
- Automatic multicam master creation.
- Camera cuts aligned to speech boundaries rather than arbitrary frame intervals.

### Local and remote ingest surfaces

- CLI supports local source, additional cameras, separate microphone, ratios, and authorized social URL input.
- Local FastAPI endpoint supports local source, additional cameras, separate microphone, URL input, aspect selection, and alternate-layout selection.
- Local API staging files are deleted after a job is copied into its permanent project folder.

### Firebase phone -> home desktop workflow

- Mobile/desktop responsive Firebase-hostable uploader.
- Google sign-in.
- Firebase Storage source upload.
- Firestore queue documents.
- Additional camera and separate mic attachments.
- User-owned social-link queue jobs.
- Transactional worker claim.
- Worker heartbeat and lease expiration.
- Stale claimed jobs automatically returned to the queue.
- Worker online/processing/uploading/publishing presence surfaced in the site.
- Firebase Admin worker validates every input Storage path belongs to the exact requesting user's job source folder before reading it.
- Completed variants uploaded back to user-scoped Storage.
- One `clipperProjects` record per starting source.
- Every resulting clip/version grouped under that source.
- Web playback and download.

### Publishing

- Optional Upload-Post adapter with no credentials committed to the repository.
- Per-job platform selection in the web UI.
- Automatic ratio selection per platform.
- Idempotency key to reduce accidental duplicate submissions.
- Publishing happens only when explicitly selected.
- Publishing errors are recorded separately; a failed social submission never discards a successfully completed edit.
- CLI publishing is also available.

## Watermark-free source behavior

The user-owned social-media workflow asks the extractor for the best video/audio stream and supports owner-authenticated browser/file cookies. When Instagram/TikTok exposes an original or cleaner source to the owner account, that clean source is used.

Clipper does not pretend it can always recover information that is no longer present. If the only stream a platform exposes already has a watermark permanently baked into the pixels, the system does not destructively erase another creator's attribution; the original local recording/export remains the guaranteed clean source.

## Bug and optimization passes completed

### Pass 1 — baseline and pipeline

- Centralized encoder selection.
- Added NVENC probe/fallback.
- Added word-edge cut snapping.
- Added interruption-safe per-clip manifest persistence.

### Pass 2 — transcription and selection

- Cached faster-whisper model instead of reloading it per clip.
- Added CUDA batching.
- Kept a CPU-only path.
- Added overlap suppression so adjacent transcript windows do not produce near-duplicate clips.

### Pass 3 — visual generation

- Filtered unsupported Commons image types so SVG/PDF assets do not reach FFmpeg as unexpected raster inputs.
- Cached the local Diffusers pipeline instead of reloading it for every cue.
- Made Commons the zero-key default and local generation optional.

### Pass 4 — rendering

- Parallelized independent output renders with conservative `RENDER_WORKERS=2` default.
- Made the 4-way alternate-layout expansion opt-in instead of multiplying every render by default.
- Added subject-analysis caching across ratio/layout renders.
- Moved smart-crop full-resolution work to FFmpeg.

### Pass 5 — smart crop optimization

The first subject tracker used OpenCV random seeks for sampled frames. That is inefficient on inter-frame codecs. It was replaced with a single FFmpeg pass that:

- seeks once,
- samples only a few frames per second,
- downscales before pixels enter Python,
- sends RGB frames directly to MediaPipe,
- caches the final subject trajectory,
- drives FFmpeg's full-resolution named crop through a `sendcmd` file.

### Pass 6 — synchronization

- Added transcript-anchor drift fit instead of assuming recorders have identical clocks.
- Added robust residual filtering for false phrase matches.
- Added waveform fallback.
- Added regression coverage for correlation sign and drift recovery.

### Pass 7 — Firebase reliability/security

- Added transactional claims.
- Added leases/heartbeats.
- Added stale-job recovery.
- Added exact user/job Storage prefix validation even though Admin SDK bypasses client security rules.
- Added owner-only Firestore/Storage client rules.
- Added `.gitignore` coverage for service-account credentials, `.env`, local media, caches, and logs.

### Pass 8 — upload and publishing

- Added mobile upload progress.
- Added source URL jobs.
- Added additional camera/mic uploads.
- Added worker presence.
- Added publishing as a non-destructive optional post-processing stage.
- Added publish status/error visibility.

### Pass 9 — CI/debugging

The first GitHub Actions test attempt exposed a test import-path problem. That was fixed by explicitly setting `PYTHONPATH` to the checked-out repository root and invoking tests through `python -m pytest`.

Subsequent CI runs passed:

- Python compile step.
- Unit/regression tests.
- Browser JavaScript ES-module parse.

Regression tests now cover:

- transcript windowing,
- word-boundary snapping,
- waveform correlation sign,
- transcript offset/drift fit,
- aspect-ratio crop geometry,
- dynamic FFmpeg crop command clamping.

## Final repository re-audit

The final tree was checked against the request after the implementation passes. The functional map is:

| Requested area | Primary implementation |
|---|---|
| Local clipping | `clipper/pipeline.py` |
| Transcript | `clipper/transcription.py` |
| Clip selection | `clipper/analysis.py`, `clip_selection.py` |
| Smart reframe | `clipper/focus.py`, `clipper/render.py` |
| B-roll/imagery | `clipper/analysis.py`, `clipper/visuals.py` |
| Split/PIP/interruption | `clipper/render.py` |
| Aspect ratios | `clipper/config.py`, `clipper/render.py` |
| Separate mic sync | `clipper/sync.py`, `clipper/multicam.py` |
| Multicam | `clipper/sync.py`, `clipper/multicam.py` |
| Device/social ingest | `clipper/media.py`, `api.py`, `web/` |
| Firebase queue | `clipper/firebase_bridge.py` |
| Home worker | `clipper/worker.py` |
| Online library | `web/app.js` |
| Firebase security | `firestore.rules`, `storage.rules`, worker path validation |
| Social publishing | `clipper/publish.py`, worker publishing stage, web controls |
| Theme | `web/styles.css`, `web/extras.css` |
| Tests | `tests/`, `.github/workflows/ci.yml` |
| Setup/docs | `README.md`, `ARCHITECTURE_REPORT.md`, `UPSTREAM_IMPORT.md` |

No unchecked requested software item remains in the ordered implementation list.

## Environment activation / smoke-test checklist

These are not missing repository features; they require the actual desktop, credentials, accounts, or source media and therefore cannot be truthfully completed by GitHub CI alone.

- [ ] Install/verify FFmpeg + FFprobe on the home desktop.
- [ ] Create `.env` from `.env.example`.
- [ ] Run a real single-camera local recording through the pipeline.
- [ ] Run a separate-microphone example and listen for sync/drift correctness.
- [ ] Run a real multicam example and inspect automatic cuts.
- [ ] Verify NVIDIA/CUDA/NVENC on the actual GPU if desired.
- [ ] Configure/deploy Firebase Auth, Firestore, Storage, Hosting, and the supplied rules.
- [ ] Put a Firebase Admin service account/ADC credential on the home desktop only.
- [ ] Start `python -m clipper.worker` and verify the site shows the worker online.
- [ ] Queue a phone upload and verify desktop pickup -> edit -> Firebase playback/download.
- [ ] Configure owner cookies if clean-source Instagram/TikTok imports require account authentication.
- [ ] Configure optional Gemini/local Diffusers settings if desired.
- [ ] Configure Upload-Post credentials and connect desired social accounts before enabling auto-publish.

The software implementation is complete; this last section is the deployment acceptance test for the specific machine/accounts that will run it.
