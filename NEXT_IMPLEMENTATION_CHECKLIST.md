# Clipper — next implementation checklist

This is the working checklist for the next Clipper session. It separates **what was hardened now** from **what still needs to be implemented or activated on the real workstation** so unfinished infrastructure is never mistaken for finished software.

## Completed in the latest hardening pass

- [x] Add regression tests that intentionally break cache, smart-cut, clip-selection, edit-plan, visual, and render-cache assumptions before accepting fixes.
- [x] Fix smart-cut cleanup so a tiny retained speech island can no longer cause deleted silence/filler gaps to be silently reintroduced.
- [x] Make media fingerprints stable across identical copies with different filesystem mtimes, improving cache reuse across Firebase/local project copies.
- [x] Sample the beginning, middle, and end of large media for cache fingerprints so same-size files with changed middle content do not collide as easily.
- [x] Rebuild clip scoring text from timestamped words after final trim/snap so scoring, hooks, Gemini reranking, and B-roll planning do not use words outside the actual clip.
- [x] Contain edit-plan logo/music assets inside the project instead of allowing `../` relative paths to escape the project root.
- [x] Cache Wikimedia Commons visuals locally by semantic query/result index so rerenders stop redownloading or silently swapping unchanged B-roll.
- [x] Cache local Diffusers images by model + prompt + generation settings so unchanged rerenders do not waste GPU work.
- [x] Include actual logo/music/B-roll asset fingerprints in the final render signature so changing an asset in place invalidates the correct cached render.
- [x] Reject obviously tiny/truncated cached video variants instead of treating any existing file as a valid finished render.
- [x] Keep the fast CI lane capable of importing/testing visual code by including `httpx` in its lightweight validation dependencies.

---

# When I get back — first activation pass

Do these before adding more product surface area. The goal is to prove one real phone -> Firebase -> A4000 desktop -> Firebase -> playback/download round trip.

## A. NVIDIA A4000 workstation

- [ ] Update/install a stable NVIDIA production or Studio driver and confirm `nvidia-smi` sees the A4000 with the expected VRAM.
- [ ] Install/verify an FFmpeg build that exposes `h264_nvenc` (and preferably `hevc_nvenc` for future archival/proxy work).
- [ ] Create the Python 3.11 virtual environment and install `requirements.txt`.
- [ ] Install the CUDA/cuDNN runtime versions required by the pinned `faster-whisper`/CTranslate2 stack.
- [ ] Run `python scripts/gpu_smoke.py` locally and require both CTranslate2 CUDA inference and NVENC encode to pass.
- [ ] Run one real 5-15 minute talking-head source through `large-v3`, starting with batch size 8 and `RENDER_WORKERS=2`.
- [ ] Watch VRAM, GPU utilization, thermals, encode utilization, wall time, and output quality; tune batch/render concurrency only after measuring.

### Acceptance gate

- [ ] Whisper runs on CUDA rather than silently falling back to CPU.
- [ ] Final H.264 delivery encodes through NVENC when `FFMPEG_ENCODER=nvenc`.
- [ ] A 10-minute representative source completes without OOM, corrupt output, runaway thermals, or audio/video drift.

## B. GitHub self-hosted A4000 Actions lane

- [ ] Register the home Windows PC as a GitHub self-hosted runner for `yadasa/Clipper`.
- [ ] Apply runner labels exactly matching the workflow: `self-hosted`, `Windows`, `X64`, `a4000`.
- [ ] Set the repository Actions variable `A4000_RUNNER_ENABLED=true`.
- [ ] Keep the runner restricted to trusted `main` pushes/manual dispatches; do not enable untrusted PR code on the home PC.
- [ ] Trigger a manual workflow and verify `NVIDIA A4000 CTranslate2 CUDA + NVENC smoke` runs instead of being skipped.
- [ ] Configure the runner/service to recover after reboot and verify it returns online automatically.

## C. Firebase production activation

- [ ] Create/select the production Firebase project.
- [ ] Enable Google Authentication and add the real Hosting domains as authorized domains.
- [ ] Create Firestore and Cloud Storage in the intended region.
- [ ] Deploy `firestore.rules`, `storage.rules`, and Firebase Hosting from this repository.
- [ ] Configure `FIREBASE_PROJECT_ID` and `FIREBASE_STORAGE_BUCKET` on the home desktop.
- [ ] Configure Application Default Credentials or a narrowly stored Firebase Admin service-account credential on the home desktop; never commit it.
- [ ] Start `python -m clipper.worker` and confirm the website reports the home desktop online.
- [ ] From the phone, upload one real source and confirm the desktop claims the job, processes it, uploads variants, and returns to idle.
- [ ] Verify another signed-in user cannot read the first user's projects or source objects.
- [ ] Test an interrupted worker/job and confirm the lease recovery path requeues it rather than losing the project.

## D. Optional external services

- [ ] Configure owner-authenticated yt-dlp cookies only if Instagram/TikTok clean-source retrieval needs them.
- [ ] Add `GEMINI_API_KEY` only if AI reranking/hooks/visual planning are desired; prove the local fallback remains usable with the key removed.
- [ ] Install `requirements-ai.txt` and choose a Diffusers model only if local generated B-roll is desired.
- [ ] Configure Upload-Post credentials and connect only the social accounts you actually want Clipper to publish to.
- [ ] Publish to private/test destinations first and verify idempotency prevents accidental duplicate posts.

---

# Must-add product features

## P0 — build these next

### 1. Interactive timeline + visual edit-plan editor

**Why:** `edit_plan.json` already makes edits non-destructive, but a creator should not have to edit JSON to use it.

- [ ] Build a browser timeline with source waveform, transcript, clip in/out handles, B-roll regions, punch-in markers, caption regions, and music track.
- [ ] Drag clip boundaries while snapping to word/sentence edges.
- [ ] Enable/disable clips and aspect-ratio variants without deleting their history.
- [ ] Edit hook text, caption preset, B-roll layout, logo, music level, and punch-ins per clip.
- [ ] Add `Preview` and `Final render` actions that modify the plan and call rerender without retranscription.
- [ ] Autosave plan revisions and show unsaved/rendering/error states clearly.

**Acceptance:** A phone/desktop browser user can change a clip, preview it, and produce a new final render without touching JSON or retranscribing.

### 2. Transcript correction + timing repair

**Why:** Caption quality and every transcript-aware edit depend on accurate words/timestamps.

- [ ] Make transcript text editable in the project UI.
- [ ] Preserve word timing for untouched text and realign only the modified sentence/region.
- [ ] Add find/replace for names, brands, jargon, and recurring transcription mistakes.
- [ ] Add dictionary/custom-vocabulary hints per brand/project.
- [ ] Mark low-confidence words for review when confidence metadata is available.
- [ ] Export corrected SRT, VTT, ASS, and plain transcript files.

**Acceptance:** Correcting one name does not require a full retranscription and every caption/export uses the corrected text.

### 3. Proxy/preview render mode

**Why:** Full 1080x1920 renders are wasteful while choosing trims, B-roll, captions, or hooks.

- [ ] Generate reusable 540p/720p proxies once per source/camera.
- [ ] Use low-resolution fast previews for edit iterations.
- [ ] Cache preview renders separately from final-delivery renders.
- [ ] Provide a clear `Draft` watermark/status in the UI, not in final output.
- [ ] Final render must return to full source resolution and delivery settings.

**Acceptance:** Common edit changes preview in seconds on the A4000 without reducing final quality.

### 4. Video B-roll + motion treatment for stills

**Why:** Static Commons images are useful fallback material but are not enough for polished short-form edits.

- [ ] Add reusable/licensed video B-roll providers with source/license metadata.
- [ ] Allow local B-roll upload and a personal reusable asset library.
- [ ] Add Ken Burns/pan/zoom/parallax treatment for still images.
- [ ] Add entrance/exit transitions with conservative defaults.
- [ ] Score semantic relevance before accepting a B-roll result.
- [ ] Allow `none` when a cutaway would be worse than the talking head.
- [ ] Cache fetched/generated assets globally by content identity where licensing allows.

**Acceptance:** B-roll can be replaced or disabled per cue, remains attribution-aware, and unchanged rerenders make no unnecessary network/model calls.

### 5. Speaker diarization + active-speaker framing

**Why:** Multicam interviews need to know *who* is talking, not merely when speech occurs.

- [ ] Add speaker diarization with stable speaker IDs across a source.
- [ ] Let the user label `Speaker 1`/`Speaker 2` with names.
- [ ] Associate face tracks/cameras with speakers where confidence is sufficient.
- [ ] Drive multicam switching, crop target, split-screen choice, and captions from active speaker.
- [ ] Keep a conservative fallback when identity/face association is uncertain.
- [ ] Prevent rapid ping-pong cuts with minimum shot duration/hysteresis.

**Acceptance:** A two-person interview follows the active speaker while preserving reaction shots and never cuts wildly on brief interjections.

### 6. Advanced speech cleanup + mastering chain

**Why:** Viewers forgive average video faster than muddy, noisy, harsh, or inconsistent speech.

- [ ] Add optional noise reduction before loudness normalization.
- [ ] Add high-pass/EQ, gentle compression, de-essing, limiter/true-peak protection, and optional de-reverb.
- [ ] Analyze clipping, noise floor, loudness, and speech intelligibility before choosing processing strength.
- [ ] Keep original audio untouched and make processing reversible in the edit plan.
- [ ] Define platform delivery presets around LUFS/true peak rather than one universal hard-coded chain.
- [ ] Add A/B audio preview.

**Acceptance:** Speech becomes clearer without pumping, musical artifacts, audible gating, or double-normalization.

### 7. Platform safe-zone compositor + preview overlays

**Why:** TikTok/Reels/Shorts UI can cover captions, hooks, logos, and faces even when the raw 9:16 frame looks correct.

- [ ] Maintain safe-zone templates for TikTok, Instagram Reels, YouTube Shorts, and other enabled targets.
- [ ] Show simulated platform UI overlays in preview only.
- [ ] Move captions/hooks/logos independently per platform when required.
- [ ] Warn when tracked faces or important B-roll fall under known UI regions.
- [ ] Save platform-specific layout overrides in the edit plan.

**Acceptance:** A creator can visually verify every target before export and captions never default under common platform controls.

### 8. Publishing scheduler + durable retry dashboard

**Why:** Publishing hooks exist, but production use needs scheduling, observability, token health, and safe recovery.

- [ ] Add per-platform scheduled publish times/time zones.
- [ ] Store a durable publish state machine separate from render completion.
- [ ] Add exponential retry/backoff only for retryable failures.
- [ ] Surface account/token expiration before a scheduled post fails.
- [ ] Preserve idempotency across worker restarts and manual retries.
- [ ] Allow retrying one failed platform without reposting successful targets.
- [ ] Log external post IDs/URLs where the provider returns them.

**Acceptance:** Killing/restarting the worker during a publish cannot create a duplicate post.

### 9. Analytics feedback loop for personalized clip ranking

**Why:** Generic `viral` scoring should eventually learn what *this creator's* audience actually watches, saves, and shares.

- [ ] Import available post metrics: views, average watch time, completion, rewatches, likes, comments, saves, shares, follows/conversions.
- [ ] Store the exact edit-plan/render signature associated with each published variant.
- [ ] Compare performance by hook type, clip score dimensions, duration, caption style, B-roll density, layout, topic, and platform.
- [ ] Learn creator-specific ranking weights only after enough data exists.
- [ ] Keep historical baseline/generic scoring available and visible.
- [ ] Never train against vanity views alone when retention/engagement is available.

**Acceptance:** The ranking report can explain why a candidate moved up/down based on the creator's own measured history.

### 10. Revision history + compare/rollback

**Why:** Non-destructive editing needs safe experimentation rather than one mutable plan file.

- [ ] Version edit plans immutably with parent revision IDs.
- [ ] Record who/what changed trims, captions, B-roll, hook, music, or layouts.
- [ ] Keep render outputs associated with the exact revision/signature.
- [ ] Add side-by-side revision comparison.
- [ ] Restore an older revision without deleting newer work.
- [ ] Add storage cleanup rules that never delete a revision still referenced by a published post.

**Acceptance:** Any rendered/published clip can be reproduced from its saved revision.

### 11. Shot-quality-aware multicam director

**Why:** A synced camera should not be selected merely because it exists.

- [ ] Score blur/focus, exposure, face visibility, occlusion, shake, framing, and shot continuity.
- [ ] Prefer the active speaker only when that camera meets a quality floor.
- [ ] Use reaction/wide shots intentionally at sentence/idea boundaries.
- [ ] Penalize cuts that are too frequent or visually discontinuous.
- [ ] Expose the automatic director decisions on the timeline for manual override.

**Acceptance:** Covering a camera or knocking it out of focus causes the director to avoid it automatically.

### 12. Thumbnail/title studio with variants

**Why:** The current representative frame is useful, but publishing performance often depends on deliberate packaging.

- [ ] Rank candidate thumbnail frames by face visibility, expression, sharpness, and composition.
- [ ] Allow frame scrubbing/manual selection.
- [ ] Add optional text treatment using the brand kit.
- [ ] Generate multiple truthful title/hook pairs from the actual clip transcript.
- [ ] Save platform-specific thumbnail variants where supported.
- [ ] Track which thumbnail/title variant was published for analytics.

**Acceptance:** The project library can produce, compare, and remember multiple packaging variants without rerendering video.

---

# P1 — production/workflow features after P0

### 13. Translation + localization

- [ ] Translate corrected transcripts/captions while preserving source meaning.
- [ ] Support bilingual subtitle modes.
- [ ] Add locale-safe fonts and line-breaking rules.
- [ ] Optionally create dubbed tracks only with explicit creator approval and clear language labels.
- [ ] Export localized caption sidecars as well as burned-in variants.

### 14. Batch ingest + watch folders

- [ ] Queue multiple files/projects from the web UI.
- [ ] Add local watch-folder profiles for camera-card/drop-folder workflows.
- [ ] Deduplicate identical source files by content identity.
- [ ] Add queue priority, pause/resume/cancel, and overnight mode.
- [ ] Prevent one failed project from blocking later jobs.

### 15. Windows tray/service worker

- [ ] Package the worker as an auto-starting Windows service/tray app.
- [ ] Show GPU, queue, active job, disk usage, last error, and Firebase connection state.
- [ ] Add graceful shutdown so active manifests/cache metadata are left consistent.
- [ ] Support safe application updates with rollback.
- [ ] Keep GitHub Actions runner lifecycle separate from the editing worker lifecycle.

### 16. Storage lifecycle, deduplication, and quota controls

- [ ] Add a content-addressable cache for reusable source/proxy/B-roll artifacts.
- [ ] Define retention windows for temporary Firebase inbox files, proxies, prepared intermediates, and old renders.
- [ ] Never delete original source or published revision artifacts without an explicit policy.
- [ ] Show local disk and Firebase Storage usage.
- [ ] Add a dry-run cleanup report before destructive cleanup.

### 17. End-to-end quality/performance benchmark suite

- [ ] Create a small legally redistributable fixture set: talking head, silence/fillers, noisy audio, two speakers, multicam drift, portrait/landscape, no-audio video.
- [ ] Record CPU/GPU wall time, VRAM peak, output size, decode/encode failures, transcript WER sample, sync error, crop stability, and render validity.
- [ ] Store baseline metrics and fail CI on major regressions where deterministic.
- [ ] Add a local A4000 benchmark command that does not run on every push.
- [ ] Produce a machine-readable benchmark JSON plus a human report.

### 18. Windows installer + first-run diagnostics

- [ ] Build a one-command/installer setup for Python/runtime dependencies.
- [ ] Detect missing FFmpeg, NVENC, CUDA/CTranslate2, Firebase credentials, writable workdir, and network connectivity.
- [ ] Offer a guided first-run checklist instead of cryptic exceptions.
- [ ] Include a diagnostics bundle generator that excludes credentials and private media.

### 19. LAN/API security hardening

- [ ] Require authentication if the FastAPI service is reachable beyond localhost.
- [ ] Serve only explicitly allowed project output files rather than exposing the entire work directory.
- [ ] Add strict upload size/type limits and media probing before expensive processing.
- [ ] Add request/job rate limits appropriate for a personal workstation.
- [ ] Tighten Firebase rules further and evaluate Firebase App Check for the hosted web app.
- [ ] Keep an audit trail for destructive/project/publish actions.

### 20. Offline-first PWA + resilient uploads

- [ ] Make the web UI installable as a PWA.
- [ ] Queue job metadata locally while offline.
- [ ] Use resumable/chunk-aware uploads for large phone recordings and survive browser/network interruptions.
- [ ] Resume without uploading already completed objects again.
- [ ] Clearly distinguish `uploaded`, `queued`, `claimed`, `processing`, `uploading results`, `publishing`, and `done`.

---

# Final acceptance matrix before calling Clipper production-ready

## Correctness

- [ ] No clip references transcript words outside its rendered timeline.
- [ ] Smart cuts never restore a gap selected for deletion.
- [ ] Captions remain synchronized after smart cuts, punch-ins, multicam sync, and rerenders.
- [ ] Separate mic/camera drift stays acceptably synchronized from beginning to end on a long real recording.
- [ ] Changing any source/trim/style/logo/music/B-roll input invalidates only the render stages that actually depend on it.
- [ ] Interrupted jobs resume/requeue without duplicate or corrupted output.

## Quality

- [ ] Subject tracking stays stable with one face, two faces, brief face loss, and fast movement.
- [ ] Portrait, square, 4:5, and landscape layouts are manually reviewed on representative clips.
- [ ] Captions/hooks/logos stay inside target-platform safe zones.
- [ ] Audio passes headphones + phone-speaker checks without pumping/clipping.
- [ ] B-roll is contextually correct and license/source metadata is retained.

## Performance

- [ ] A4000 CUDA transcription is confirmed in a real job.
- [ ] NVENC is confirmed in a real final render.
- [ ] Cache hit rerenders skip transcription, resolved B-roll downloads, generated-image inference, and unchanged final renders.
- [ ] Proxy preview is measurably faster than final render once implemented.
- [ ] Long projects do not grow Firebase inbox/local temporary storage without bound.

## Security/reliability

- [ ] Secrets are outside Git and logs.
- [ ] Firebase user isolation is tested with two accounts.
- [ ] Self-hosted Actions never execute untrusted PR code.
- [ ] Local API is localhost-only or authenticated when exposed to LAN.
- [ ] Publishing retries cannot duplicate successful posts.
- [ ] Backup/restore of project plan + transcript + source reference is proven.

## Definition of done

Clipper is ready for daily production use when one real recording can be uploaded from the phone, processed on the A4000 workstation, edited non-destructively from the browser, rerendered quickly, reviewed in platform-safe previews, downloaded/published, recovered after a forced interruption, and reproduced later from its exact saved revision — with all automated tests and the real A4000 smoke lane green.
