# Architecture and capability report

## Executive summary

`mutonby/openshorts` is a broad AI video platform. Its clip-generator path already had a useful core: download/local ingest, faster-whisper transcription, Gemini moment selection, scene analysis, MediaPipe/YOLO smart reframing, FFmpeg rendering, subtitles/effects, and social publishing. It also contains a separate AI UGC generator and YouTube Studio functionality that are outside the narrower goal of this repository.

Clipper keeps the useful clipping ideas but reshapes the system around a **personal, local-first editing worker**. The home computer is the trusted compute machine; Firebase is a handoff/library layer rather than the renderer. That makes local GPU/CPU resources useful without forcing all video processing into a paid cloud GPU.

## How the OpenShorts clipping path works

The inspected upstream code and README describe this flow:

1. **Ingest** — local media or remote media downloaded with `yt-dlp`.
2. **Transcribe** — faster-whisper creates transcript segments and word-level timestamps.
3. **Analyze scenes** — PySceneDetect and visual heuristics divide the source into scenes.
4. **Choose short-form moments** — Gemini receives transcript/timing context and returns 15–60 second moments intended to stand alone and perform well as shorts.
5. **Reframe** — MediaPipe face detection and YOLO person detection follow the subject for a 9:16 crop; scene logic can choose other layouts when a simple tracked crop is inappropriate.
6. **Render** — FFmpeg extracts and encodes the selected moments, adds subtitles/overlays/effects, and produces delivery files.
7. **Publish/store** — integrations can back up outputs and send them to social platforms.

A particularly good upstream optimization is the `reframe_v2.py` design: Python performs low-resolution analysis, while FFmpeg performs the full-resolution crop/scale/encode. This avoids piping every full-resolution frame through Python/OpenCV.

## Upstream capabilities

### Strong clipping features

- 3–15 AI-selected moments per long-form source.
- 15–60 second clip duration contract.
- Word-level subtitle timing.
- MediaPipe face tracking with YOLO person fallback.
- Scene-aware smart vertical reframing.
- FFmpeg-native encode path.
- Hook overlays and effect generation.
- Optional multilingual dubbing.
- Async processing and social publishing integrations.

### Broader features not required for Clipper's primary goal

- AI UGC actor generation.
- AI talking-head/lip-sync generation.
- AI thumbnail generation.
- YouTube title/description studio.
- Public SEO video/avatar galleries.
- Hosted billing/metering infrastructure.

Those are useful product features, but carrying them into a personal clipping workstation would increase dependency count, operational complexity, and maintenance without improving the core clipping job.

## Speed

OpenShorts' README states its own reference numbers as:

- **Self-hosted CPU:** about **5–8 minutes for an 8-minute video**.
- **Hosted NVIDIA GPU:** about **50 seconds for an 8-minute video**.

Those are upstream claims, not a benchmark of Clipper on your desktop. Actual Clipper time depends heavily on:

- Whisper model and CPU/GPU.
- Source codec/resolution/frame rate.
- Number and duration of selected clips.
- Number of requested aspect ratios.
- Whether alternate split/PIP/interruption versions are enabled.
- Whether local Diffusers generation is enabled.
- x264 versus NVENC.

### Main expensive stages

1. Transcription.
2. Optional visual generation.
3. Re-encoding each output variant.
4. Multicam master construction when extra cameras are used.

### Performance work implemented in Clipper

- Faster-whisper model is cached instead of loaded per clip.
- CUDA uses batched faster-whisper when available; CPU uses int8.
- NVENC is probed once and can replace x264 automatically.
- Face analysis samples low-resolution frames rather than inspecting every full-resolution frame.
- Subject trajectories are cached per source clip.
- FFmpeg performs the actual high-resolution dynamic crop/scale/encode.
- Diffusers pipeline is cached instead of reloaded for every visual cue.
- Independent aspect/layout renders run in a conservative thread pool (`RENDER_WORKERS=2` by default).
- Alternate layout renders are now opt-in because four layouts across several ratios can multiply encoding work dramatically.
- Project manifests are persisted after each completed clip so an interrupted long run retains completed work.

## What OpenShorts was missing for this specific personal workflow

For a true local-first creator clipping workstation, the upstream clipping system did not directly provide the exact requested end-to-end flow below:

- Mobile upload that hands a job to a specific home desktop.
- Firebase Storage/Firestore queue and finished-media library centered around a source-video folder.
- Home worker presence, job leasing, heartbeat, and expired-job recovery.
- Separate microphone alignment to camera audio using spoken-word timestamps plus drift correction.
- Arbitrary additional-camera alignment and automatic multicam cuts.
- Several independent aspect-ratio versions for the same source clip.
- Transcript-aware B-roll/imagery pulled or generated at specific moments.
- Multiple visual composition alternatives (split, PIP, interruption) for each ratio.
- A personal beige/brown web upload/library interface.
- Explicit user-owned Instagram/TikTok source recovery flow.
- Security validation that a Firebase Admin worker only reads media from the requesting user's exact queued-job source folder.
- Optional publishing targets attached directly to the editing job so completed clips can be submitted automatically.

These are now implemented in Clipper's architecture.

## Clipper architecture

```text
Phone / laptop browser
        |
        | Firebase Auth
        v
Firebase Hosting UI
        |
        +--> Firebase Storage: users/<uid>/sources/<job>/...
        |
        +--> Firestore: clipperJobs/<job>
                         status=queued
                              |
                              v
                    Home desktop worker
                    -------------------
                    transactional claim
                    lease + heartbeat
                    download source(s)
                    local editing pipeline
                    upload results
                    optional publish
                              |
                  +-----------+-----------+
                  |                       |
                  v                       v
         Firebase Storage          Upload-Post
         finished variants         selected networks
                  |
                  v
       clipperProjects/<job>
                  |
                  v
        Browser project library
        playback + download
```

## Local editing pipeline

### 1. Ingest

`clipper/media.py`

- Local file copy.
- `yt-dlp` import for authorized social links.
- Highest-quality video+audio selection.
- Optional browser/file cookies for owner-authenticated source access.
- Ownership/permission acknowledgement required for recognized social platforms.

### 2. Transcribe

`clipper/transcription.py`

- faster-whisper.
- Word timestamps.
- VAD.
- Cached model.
- CUDA batching or CPU int8 fallback.

### 3. Sync external recordings

`clipper/sync.py` and `clipper/multicam.py`

Primary method:

- Normalize transcript words.
- Find unique repeated n-gram anchors shared by two recordings.
- Fit the mapping:

```text
primary_time = intercept + rate * secondary_time
```

- `intercept` is start offset.
- `rate` captures recorder clock drift.
- Robust residual filtering removes bad text matches.

Fallback method:

- Decode mono PCM envelope with FFmpeg.
- FFT cross-correlate the two envelopes.
- Recover audio offset even when transcription does not provide enough anchors.

For a separate mic, the synchronized mic replaces camera audio. For extra cameras, camera time ranges are mapped to the primary timeline and cuts occur near speech-segment boundaries.

### 4. Pick clips

`clipper/analysis.py` and `clip_selection.py`

- Build overlapping transcript windows.
- Score hooks/questions/numeric specificity/density/completeness locally.
- Avoid strongly overlapping duplicate selections.
- Snap boundaries to word edges.
- Optionally ask Gemini to rerank the candidate list with structured JSON output.

The local mode means clipping still works without a Gemini key.

### 5. Plan imagery

`clipper/analysis.py` and `clipper/visuals.py`

- Divide each selected moment into short semantic cue windows.
- Generate a search query and image prompt from the words being spoken.
- Optional Gemini planning improves semantic specificity.
- Default provider searches Wikimedia Commons and saves attribution metadata.
- Optional local Diffusers generation can create missing imagery.

### 6. Reframe and compose

`clipper/focus.py` and `clipper/render.py`

- Sample face positions at low resolution.
- Sticky target selection prevents a background face from immediately stealing focus.
- Exponential smoothing reduces crop jitter.
- FFmpeg `sendcmd` drives the named crop filter at full resolution.
- Center crop is the safe fallback when no face is detected.
- Render 9:16, 4:5, 1:1, or 16:9.
- Overlay transcript-matched visuals as:
  - split screen,
  - picture-in-picture,
  - full-screen/interruption.
- Automatic mode varies layout preference by aspect ratio.
- Optional alternate mode renders each composition explicitly for comparison.

### 7. Captions and delivery

- ASS captions from word timestamps.
- H.264/AAC MP4.
- 1080-class social dimensions.
- `yuv420p` compatibility.
- `+faststart` for progressive web playback.
- loudness normalization.
- metadata/chapter scrub.
- x264 or NVENC.

### 8. Store and publish

- Every source is its own project folder.
- Every selected clip has each requested ratio/composition beneath it.
- Firebase UI groups finished versions under the original source.
- Users can watch and download every variant.
- Publishing is explicit/optional; an edit is never discarded because a social submission failed.

## Capability matrix after the Clipper work

| Capability | Status |
|---|---|
| Local video -> automatic shorts | Implemented |
| Local CPU operation | Implemented |
| NVIDIA transcription/encoding acceleration | Implemented |
| Word-level captions | Implemented |
| Smart subject reframe | Implemented |
| Multiple aspect ratios | Implemented |
| Transcript-aware pulled imagery | Implemented |
| Local AI-generated imagery | Implemented, optional model install |
| Split/PIP/interruption B-roll | Implemented |
| Alternate visual versions | Implemented, opt-in |
| Separate microphone sync | Implemented |
| Multicam sync/drift correction | Implemented |
| Automatic multicam switching | Implemented |
| Mobile Firebase upload | Implemented |
| Home computer job pickup | Implemented |
| Worker leases/heartbeat | Implemented |
| Finished Firebase library | Implemented |
| Browser playback/download | Implemented |
| User-owned social URL import | Implemented |
| Clean/original source preference | Implemented where the platform exposes such a source |
| Automatic social publishing | Implemented, optional credentials |
| Beige/brown muted UI | Implemented |
| CI syntax/unit validation | Implemented |

## Important boundaries

### Watermark-free social imports

Clipper prefers the best/original stream the extractor can access and supports logged-in owner cookies. That can recover a clean source when the social platform exposes one. It is intentionally **not** an arbitrary pixel-level watermark eraser. If a watermark is permanently baked into the only media stream the platform supplies, the cleanest workflow is to use the original camera/export file instead.

### Imagery rights

Commons assets are accompanied by attribution metadata because reusable assets can still have license/credit conditions. Locally generated imagery avoids stock lookup but adds GPU/model cost.

### “All platforms”

The publishing adapter is intentionally separated from core editing. Today the included Upload-Post adapter covers its supported network set; credentials and connected social accounts remain local environment configuration, not repository secrets.

## Remaining environment-dependent work before calling a particular computer production-ready

The code path is implemented, but these cannot be validated from GitHub CI alone:

- Install an FFmpeg build with the filters/codecs you intend to use.
- Download/run your chosen Whisper model on the actual desktop.
- Confirm NVIDIA driver/NVENC if using GPU encoding.
- Configure Firebase Auth/Firestore/Storage/Hosting and deploy the supplied rules.
- Provide a Firebase Admin service account/ADC on the home computer.
- Configure optional Gemini/Diffusers/Upload-Post credentials.
- Run at least one representative end-to-end recording through each workflow you plan to use (single-camera, separate mic, multicam, phone upload, and social-link import).

Those are deployment/smoke-test steps, not missing software features. `IMPLEMENTATION_REPORT.md` contains the final verification checklist.
