# Clipper

Local-first automated video clipping for recordings you make, with an optional Firebase handoff when you are away from your desktop.

**Auto Mode is the default creator workflow:** give Clipper the raw main video, optional extra cameras, and optional separate microphone. It builds a synchronized precomp, chooses the usable audio track, transcribes the authoritative timeline, creates one globally cleaned master, finds the strongest coherent short-form stories, optionally stitches related chronological source slices, chooses edit intensity, adds contextual visuals/audio treatment, places captions as the final visual layer, quality-checks the deliveries, and returns ready-to-review clips. Manual mode remains available when you want explicit control over each edit switch.

## What is implemented

- First-class **Auto Mode** across CLI, local API, Firebase worker, and web uploader, with a durable per-project automation-stage report.
- Raw-camera/precomp workflow: waveform-first synchronization, optional transcript-based refinement for weak matches, clock-drift correction, multicam assembly, and separate-mic replacement.
- Automatic authoritative-audio selection using signal level, clipping, and activity checks; a clearly broken separate mic can fall back to camera audio.
- One global clean master before semantic selection, so silence/filler cuts and all remapped word timestamps share a single canonical timeline.
- Adaptive coherent clip selection: target duration and number of clips respond to speaking pace/source length instead of forcing every source into one fixed duration.
- Non-destructive coherent story stitching: a clip can combine a few chronological related source ranges while skipping an irrelevant passage; speech is never reordered.
- Per-clip automatic edit-intensity profiles for B-roll density, punch-ins, hook treatment, and caption preset.
- Automated delivery QA with dimensions/duration/stream/pixel-format checks and a simpler visual fallback when an optional complex render fails validation.
- Local file ingest and authorized Instagram/TikTok/YouTube/Facebook/X link ingest through `yt-dlp`; arbitrary remote URLs are rejected by the automatic social-ingest path.
- faster-whisper transcription with word timestamps, VAD, cached model loading, reusable CUDA batched pipelines when available, and CPU int8 fallback.
- A4000-aware local hardware profiling: automatic CUDA Whisper model/batch selection and NVENC preference when available.
- Multi-dimensional clip scoring for hook, clarity, specificity, payoff, pace, completeness, and Auto Mode coherence/duration-fit metrics, plus topic-diversity filtering and optional Gemini reranking.
- Word-boundary clip snapping so cuts avoid chopping spoken words.
- Conservative silence tightening and filler-word removal with pause guardrails; automatic deletion is limited to reliable disfluencies rather than context-dependent words.
- Automatic transcript-driven emphasis punch-ins.
- Subject-aware reframing: low-resolution sampled MediaPipe face analysis produces a smoothed crop trajectory; FFmpeg performs the full-resolution dynamic crop natively. Tracking cache is bounded and source-replacement-aware.
- 9:16, 4:5, 1:1, and 16:9 delivery variants at social-friendly 1080-class dimensions.
- Karaoke active-word captions plus clean and minimal caption presets with portrait safe zones. Captions are the final visual filter after reframe/B-roll/logo/hook composition.
- Truthful opening hook/title cards, generated locally or optionally refined with Gemini.
- Reusable brand kits for font, caption colors, accent color, logo, logo position, and hook treatment.
- Optional background music with transcript-aware speech ducking and final loudness normalization.
- Context-aware B-roll planning tied to spoken phrases and corrected against word timestamps before render.
- Ordered B-roll resolver with a personal local library, optional Pexels stock video, optional Pixabay stock video, Wikimedia Commons imagery, and optional local Diffusers generation.
- Provider-specific B-roll relevance scoring that can genuinely reject a weak result, plus exact-asset repeat suppression within each clip.
- Video and still-image B-roll composition as split-screen, picture-in-picture, or full-screen/interruption edits.
- B-roll search/download caching, bounded atomic downloads, project-local materialization, provenance metadata, and manual-asset preservation.
- Automatic speech-boundary multicam cuts.
- H.264/AAC MP4 output, `yuv420p`, loudness normalization, metadata scrubbing, and `+faststart` for web/social delivery.
- CPU x264 or NVIDIA NVENC output with automatic fallback.
- Non-destructive Edit Plan V2 files: change trims, stitched source ranges, enabled clips, ratios, layouts, captions, hooks, brand, music, and auto decisions, then rerender without retranscribing.
- Stage and render caching with source-aware memoization, artifact validation, no-op prepared-stage skipping, and interruption-safe atomic state/intermediate writes.
- Per-clip social metadata and thumbnails for the library/publishing layer.
- Local FastAPI processing API and rerender endpoint with serialized heavy jobs, bounded atomic upload staging, supported-social URL validation, and finished-output-only media serving.
- Firebase Hosting uploader/library, Firebase Auth, Storage, Firestore queue, desktop worker leases/heartbeats, transactional stale-job recovery, grouped source folders, playback, thumbnails, edit-plan/automation-report download, and video download.
- Firebase worker inbox downloads are atomic and temporary inbox files are cleaned after each job; browser clients cannot overwrite worker-produced project outputs.
- Optional automatic social publishing through Upload-Post. Publishing is opt-in per queued job and publishing failure does not discard a completed edit.
- Beige/brown responsive interface with muted accents.
- GitHub Actions quality, dependency, FFmpeg feature, Auto Mode clean-master/QA smoke, and optional self-hosted A4000 CUDA/NVENC tests on every push.

The complete Auto Mode order and rationale are documented in `AUTO_MODE.md`. The creator-workflow additions from the V2 pass are documented in `FEATURES_V2.md`. The current implementation backlog is `NEXT_IMPLEMENTATION_CHECKLIST.md`, B-roll internals are documented in `BROLL_ARCHITECTURE.md`, and repository engineering expectations are in `QUALITY_STANDARDS.md`.

## Auto Mode

The important ordering rule is **establish one authoritative timeline before semantic editing**:

```text
raw video/cameras/mic
  -> ingest + validate
  -> sync / precomp + authoritative audio choice
  -> transcribe synchronized precomp
  -> global silence/filler clean master + timestamp remap
  -> coherent slice selection / optional chronological story stitching
  -> per-clip automatic edit decisions
  -> reframe + B-roll + hook/logo + music/audio composition
  -> captions as the final visual filter
  -> delivery QA / simple fallback if required
  -> thumbnails + metadata + library
```

This avoids independently tightening the same pause differently in multiple candidates and avoids making clip-selection decisions against timestamps that are about to move. Waveform sync happens before transcription because it is cheap; transcript sync is an escalation path only for ambiguous matches. See `AUTO_MODE.md` for the detailed architecture and tuning controls.

## Why this is not a byte-for-byte OpenShorts mirror

Clipper was bootstrapped from the public `mutonby/openshorts` project. OpenShorts' root code is MIT-licensed, but its `cloud/` directory has a separate commercial license that prohibits redistribution in another product. Because this repository is public, Clipper does **not** copy that `cloud/` source. The Firebase queue/worker/library in this repository is an independent implementation. Large upstream demo media was also omitted because it is not runtime code.

The MIT-licensed `ffmpeg_utils.py` and `clip_selection.py` helpers were imported and adapted as baseline primitives. See `UPSTREAM_IMPORT.md` and `ARCHITECTURE_REPORT.md` for the full audit.

## Local setup

Requirements:

- Python 3.11 recommended.
- FFmpeg and FFprobe available on `PATH`.
- NVIDIA drivers/CUDA runtime as needed for GPU inference.
- An FFmpeg build with NVENC if you want GPU encoding.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env`. Auto Mode is enabled by default, and the template leaves hardware tuning values blank so the auto-profile can choose them.

For optional local Diffusers image generation:

```bash
pip install -r requirements-ai.txt
```

## RTX A4000 profile

With `AUTO_HARDWARE_PROFILE=1`, Clipper checks the local NVIDIA/CUDA/NVENC environment. When it detects an RTX A4000-class workstation, the built-in profile prefers:

```text
Whisper model: large-v3
Whisper device: cuda
Whisper batch: 8
FFmpeg encoder: h264_nvenc when the local FFmpeg/driver probe succeeds
Parallel output renders: 2
```

Explicit non-empty environment values override the automatic profile. Empty tuning values in `.env` intentionally count as “auto.”

Inspect the workstation profile with:

```bash
python -m clipper.cli profile
```

### Process a local recording

Auto Mode is the default:

```bash
python -m clipper.cli process "C:\Videos\recording.mp4" --ratio 9:16 --ratio 16:9
```

Extra camera and separate microphone:

```bash
python -m clipper.cli process primary.mp4 --camera camera2.mp4 --camera camera3.mp4 --mic recorder.wav --ratio 9:16
```

Explicit manual mode:

```bash
python -m clipper.cli process primary.mp4 --mode manual --ratio 9:16
```

Branding and music:

```bash
python -m clipper.cli process primary.mp4 --ratio 9:16 --brand brand.json --music music.mp3
```

Useful creator controls:

```text
--mode auto|manual Choose one-click pipeline or explicit manual path
--alternates        Also render split/PIP/interruption alternatives
--no-smart-cut      Keep original pauses / disable Auto clean-master cuts
--keep-fillers      Do not remove safe-to-cut filler words
--no-punch-ins      Disable emphasis zooms
--no-hook           Disable the opening hook card
--no-cache          Force expensive stages to run again
```

### Non-destructive rerender

Every project contains `edit_plan.json`. Modify that plan to change clip trims, stitched ranges, disable a clip, change aspect ratios/layout modes, caption preset, hook text, or other edit options, then rerender without rerunning transcription:

```bash
python -m clipper.cli rerender data/projects/<project-id>
```

Render signatures prevent unchanged variants from being encoded again.

## Importing your own social post

```bash
python -m clipper.cli process "https://www.instagram.com/reel/..." --own-content --ratio 9:16
```

Social-link import requires the ownership/permission acknowledgement and is limited to supported Instagram, TikTok, YouTube, Facebook, and X hosts. Clipper asks `yt-dlp` for the highest-quality source exposed by the platform. If the platform makes an original/cleaner source available to your logged-in account, configure either `YTDLP_COOKIES_FILE` or `YTDLP_COOKIES_FROM_BROWSER` so the home machine can access it.

Clipper does not erase a baked-in watermark from another creator's media. The intended flow is to recover the clean source of media you own.

## Context-aware B-roll

The default resolver is a provider waterfall:

```env
BROLL_AUTO_INSERT=1
BROLL_PROVIDERS=local,pexels,pixabay,commons,diffusers
BROLL_LIBRARY=
PEXELS_API_KEY=
PIXABAY_API_KEY=
DIFFUSION_MODEL=
VISUAL_PROVIDER=auto
```

Unavailable providers are skipped, so the default configuration works without every optional API key. `VISUAL_PROVIDER=none` disables automatic B-roll. The legacy `commons`, `diffusers`, and `auto` values remain supported.

The pipeline works in two stages:

1. visual planning identifies concrete spoken ideas and assigns cue windows;
2. before provider resolution/render fan-out, Clipper matches the cue transcript back to word timestamps and snaps the insert to the phrase it actually illustrates.

The aligned cue is then reused across aspect ratios/layouts instead of running fuzzy transcript matching separately for every output. This prevents relevant B-roll from appearing several seconds early or late and avoids duplicate timing work.

### Personal B-roll library

Point `BROLL_LIBRARY` to reusable media you own. Clipper searches filename/folder context and optional JSON metadata. A sidecar can be named either `asset.mp4.json` or `asset.json`:

```json
{
  "title": "Coffee beans roasting in drum",
  "description": "Close-up of a small-batch coffee roaster",
  "tags": ["coffee", "beans", "roasting"],
  "creator": "TJ",
  "license": "owned"
}
```

### Stock and generated sources

- **Pexels:** optional stock-video search when `PEXELS_API_KEY` is configured.
- **Pixabay:** optional stock-video search when `PIXABAY_API_KEY` is configured.
- **Wikimedia Commons:** no-key still-image fallback with attribution metadata.
- **Diffusers:** optional local generated-image fallback when `DIFFUSION_MODEL` is configured.

Remote search payloads and downloaded stock assets are cached. Selected assets are hard-linked or copied into the project for reproducibility. Downloads are size-bounded and atomically finalized. Short video B-roll is stream-looped only for the cue window, and B-roll audio is never mapped into the final creator audio. The configured relevance threshold can reject weak results, and the same exact asset is not automatically reused for a later cue in the same clip.

See `BROLL_ARCHITECTURE.md` for provider contracts, caching, relevance, repeat avoidance, provenance, and extension rules.

Gemini remains optional. Without it, deterministic local heuristics select clips, hooks/metadata, and spoken-phrase B-roll cues. With `GEMINI_API_KEY`, Gemini can rerank candidates and refine hook, metadata, and B-roll planning.

## Encoder/performance controls

The recommended `.env` leaves these blank with `AUTO_HARDWARE_PROFILE=1`:

```env
WHISPER_MODEL=
WHISPER_DEVICE=
WHISPER_BATCH_SIZE=
FFMPEG_ENCODER=
RENDER_WORKERS=
```

Set them only when overriding auto-tuning. Subject tracking, transcription, prepared edit stages, B-roll searches/assets, local image models, media probes/fingerprints, and final render signatures use caching to avoid repeating unchanged work. Prepared video intermediates are skipped entirely when the smart-cut/punch-in plan contains no actual transformation.

## Local API

Start the development API on loopback by default:

```bash
uvicorn api:app --host 127.0.0.1 --port 5175
```

Endpoints:

- `GET /api/health` — includes the detected hardware profile, default automation mode, and configured local upload limit.
- `GET /api/projects`
- `GET /api/jobs/{job_id}`
- `GET /media/{project_id}/clips/...` — serves only finished clip media, not source recordings/transcripts/internal worker data.
- `POST /api/process` — accepts `automation_mode=auto|manual`, primary media, extra cameras, separate mic, music, logo, brand fields, and edit-intelligence controls.
- `POST /api/projects/{project_id}/rerender` — rerenders the existing editable plan without retranscription.

Local uploads are staged in chunks, bounded by `LOCAL_MAX_UPLOAD_MB` (8 GB by default), and atomically finalized. Heavy process/rerender jobs are serialized by default so concurrent requests do not make multiple GPU/FFmpeg pipelines fight for the same workstation resources.

Keep the API on loopback unless you deliberately add authentication before exposing it on a LAN or other network. LAN authentication remains tracked in `NEXT_IMPLEMENTATION_CHECKLIST.md`.

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

From the hosted site you can choose Auto or Manual, upload raw video/camera/mic files from a phone, add a logo/music bed, or paste an authorized social link. Auto is selected by default. Firestore receives the job, the home worker claims it transactionally, keeps a lease alive while editing, uploads completed files/thumbnails/edit plan/automation report back to Storage, and writes a grouped project record for the browser library.

The worker validates that Storage source paths belong to the requesting user's exact job folder before the Admin SDK reads them. Lease-expiry recovery re-checks state inside a transaction so a heartbeat cannot race a stale-job requeue. Downloads are staged atomically, per-job inbox data is removed after processing, and worker-produced project Storage paths are read-only to browser clients under `storage.rules`.

## Automatic publishing

Configure the home desktop only:

```env
UPLOAD_POST_API_KEY=
UPLOAD_POST_USER=
```

Then select platforms in the web uploader. If the post description is blank, Clipper uses generated social metadata. Publishing remains opt-in. Clipper chooses the closest rendered aspect ratio per platform and uses an idempotency key to reduce accidental duplicate submissions. Finished files remain preserved if publishing fails.

CLI publishing is also available:

```bash
python -m clipper.cli publish data/projects/<project>/manifest.json --platform tiktok --platform instagram
```

## Output organization

Each Auto Mode source creates approximately:

```text
data/
  cache/
    broll/
      search/
      assets/
  projects/<project-id>/
    manifest.json
    auto_stages.json
    transcript_precomp.json
    transcript.json
    clip_candidates.json
    edit_plan.json
    source/
      precomp-synced-audio.mp4
      precomp-multicam.mp4
      precomp-clean-master.mp4
      clean-master-edl.json
    sync/
    prepared/<clip-id>/
    visuals/<clip-id>/
      timeline.json
      cue_00/...
    qa/<clip-id>.json
    clips/<clip-id>/
      render-cache.json
      <ratio>/...mp4
      <ratio>/...jpg
```

Not every optional precomp file is created on every run; no-op stages reuse the existing authoritative source. The Firebase library mirrors the same concept: one project per starting source, with every finished clip and selected aspect-ratio/composition version grouped beneath it.

## GitHub Actions validation

`.github/workflows/ci.yml` runs on every push, every pull request, and manual dispatch. It has three layers:

1. **Fast quality gate** — Python compilation, Ruff correctness scan, unit/regression tests including Auto Mode selection/stitching, browser JavaScript parse, and Firebase JSON validation.
2. **Runtime + FFmpeg feature smoke** — installs the real runtime dependency set and FFmpeg, imports the full application/API, verifies required FFmpeg filters, then executes synthetic clean-master/audio inspection, smart-cut, silent-video, punch-in, video-B-roll, karaoke-caption, hook, brand/logo, music-ducking, separate-mic, multicam, delivery-QA, and 9:16 render paths.
3. **Optional A4000 GPU smoke** — verifies CUDA, the A4000 identity, `h264_nvenc`, a real NVENC encode, and ffprobe result on your own desktop runner.

The A4000 job is intentionally opt-in so GitHub-hosted jobs do not wait for a machine that is offline. To activate it, register the desktop as a self-hosted Windows x64 runner with the `a4000` label and set repository Actions variable `A4000_RUNNER_ENABLED=true`.

## Reports and engineering docs

- `AUTO_MODE.md` — exact one-click pipeline order, rationale, seven added automation features, and tuning controls.
- `NEXT_IMPLEMENTATION_CHECKLIST.md` — canonical remaining-work and acceptance checklist.
- `BROLL_ARCHITECTURE.md` — context-aware B-roll provider/timing/cache architecture.
- `QUALITY_STANDARDS.md` — architecture, failure-mode, media-quality, and testing expectations.
- `FEATURES_V2.md` — creator features from the V2 pass.
- `ARCHITECTURE_REPORT.md` — OpenShorts architecture/capability audit and Clipper redesign.
- `UPSTREAM_IMPORT.md` — imported vs intentionally excluded upstream material.
- `IMPLEMENTATION_REPORT.md` — implementation status, bug/optimization passes, environment-dependent validation.

## License

The imported OpenShorts core helpers retain the upstream MIT license notice in the repository root. No OpenShorts `cloud/` commercial source is redistributed here.