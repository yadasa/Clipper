# Clipper — Next Implementation Checklist

This file is the canonical engineering backlog for the next Clipper sessions. Completed items reflect code that is already in the repository; unchecked items require workstation activation, real-media validation, or additional product work.

## Current hardening baseline

- [x] Regression coverage for cache invalidation, smart cuts, clip selection, project assets, visual reuse, and damaged render outputs.
- [x] Smart-cut intervals never bridge across a region explicitly selected for removal.
- [x] Automatic filler removal is limited to reliable disfluencies instead of deleting context-dependent words such as `actually`, `like`, or `basically`.
- [x] Smart-cut and punch-in intermediates are written to partial files and atomically finalized so interrupted encodes are not reused as valid media.
- [x] No-op smart-cut/punch-in analysis skips unnecessary intermediate video encodes.
- [x] Media fingerprints are copy-stable, sample the beginning/middle/end of large files, and are memoized without changing their content identity.
- [x] FFprobe metadata is memoized and invalidated when a file is replaced in place.
- [x] Face-tracking cache is bounded and invalidates on source file replacement instead of growing for the lifetime of the desktop worker.
- [x] CUDA batched Whisper pipelines are reused across transcriptions rather than reconstructed for each source.
- [x] Clip scoring, hooks, metadata, and visual planning use only transcript words inside the selected clip.
- [x] Rerender logo/music assets are contained inside the project instead of escaping through relative paths.
- [x] Commons and Diffusers still-image assets are cached across unchanged rerenders.
- [x] Render signatures include source, B-roll, logo, music, trim, layout, caption, and hook dependencies.
- [x] Tiny/truncated cached render files are rejected.
- [x] Context-aware B-roll provider architecture is isolated in `clipper/broll.py` and documented in `BROLL_ARCHITECTURE.md`.
- [x] Automatic B-roll timing is planned from spoken phrases, corrected against word timestamps once, and reused across aspect-ratio/layout render fan-out.
- [x] Both still-image and video B-roll can be composed as split-screen, PIP, or full-screen interruption.
- [x] Remote B-roll search responses/downloads are cached and selected assets are materialized inside the project.
- [x] B-roll relevance scores act as real rejection gates rather than fixed minimum scores that force unrelated provider results through.
- [x] Exact B-roll asset identity is tracked within a clip so later automatic cues do not reuse the identical cutaway.
- [x] Provider failures degrade to the next configured source rather than failing the whole edit.
- [x] Local API heavy jobs are serialized by default to avoid competing GPU/FFmpeg workloads on one workstation.
- [x] Local API uploads are byte-bounded, chunk-staged, and atomically finalized; unsupported arbitrary remote URLs are rejected before processing.
- [x] Local API media serving exposes only finished project clip media instead of mounting the entire work directory.
- [x] Firebase stale-job recovery transactionally re-checks the lease before requeueing, removing the heartbeat/requeue race.
- [x] Firebase inbox downloads are atomic and per-job temporary inbox data is removed after processing.
- [x] Firebase project records retain B-roll visual-cue provenance for the web library.
- [x] Browser upload failures clean up already-uploaded Firebase source objects instead of leaving orphaned blobs.
- [x] Firebase Storage rules allow client writes only to the user's source inbox; worker-produced project outputs are client read-only.

---

# When I get back — workstation activation first

The first goal is one proven phone -> Firebase -> A4000 desktop -> Firebase -> browser playback/download loop.

## A. NVIDIA A4000 workstation

- [ ] Install/update a stable NVIDIA production or Studio driver and confirm `nvidia-smi` sees the A4000 and expected VRAM.
- [ ] Install/verify FFmpeg and FFprobe on `PATH`.
- [ ] Confirm FFmpeg exposes `h264_nvenc`; verify `hevc_nvenc` as an optional archival/proxy path.
- [ ] Create the Python 3.11 virtual environment and install `requirements.txt`.
- [ ] Install the CUDA/cuDNN runtime expected by the pinned faster-whisper/CTranslate2 stack.
- [ ] Run `python scripts/gpu_smoke.py` and require both CTranslate2 CUDA inference and NVENC encode to pass.
- [ ] Run a representative 5-15 minute talking-head recording through `large-v3` with batch 8 and `RENDER_WORKERS=2`.
- [ ] Measure VRAM, GPU utilization, encode utilization, temperature, wall time, and output size before tuning concurrency.

### A4000 acceptance gate

- [ ] Whisper is confirmed on CUDA rather than silent CPU fallback.
- [ ] Final H.264 delivery is confirmed on NVENC when requested.
- [ ] A representative 10-minute source completes without OOM, corruption, runaway thermals, or A/V drift.

## B. GitHub self-hosted A4000 lane

- [ ] Register the home Windows PC as a self-hosted runner for `yadasa/Clipper`.
- [ ] Apply runner labels `self-hosted`, `Windows`, `X64`, `a4000`.
- [ ] Set repository Actions variable `A4000_RUNNER_ENABLED=true`.
- [ ] Keep the runner restricted to trusted `main` pushes/manual dispatch; never execute arbitrary PR code on the home PC.
- [ ] Trigger the workflow manually and verify the A4000 CUDA/NVENC smoke runs instead of being skipped.
- [ ] Configure runner recovery after reboot and verify it returns online unattended.

## C. Firebase production activation

- [ ] Create/select the production Firebase project.
- [ ] Enable Google Authentication and authorize the real Hosting domains.
- [ ] Create Firestore and Cloud Storage in the intended region.
- [ ] Deploy `firestore.rules`, `storage.rules`, and Firebase Hosting from this repository.
- [ ] Configure `FIREBASE_PROJECT_ID` and `FIREBASE_STORAGE_BUCKET` on the home desktop.
- [ ] Configure ADC or a narrowly stored Firebase Admin service-account credential; never commit it.
- [ ] Start `python -m clipper.worker` and confirm the site reports the desktop online.
- [ ] Upload a real source from the phone and verify claim -> process -> upload outputs -> idle.
- [ ] Test with a second account and verify cross-user Firestore/Storage isolation.
- [ ] Kill the worker during a job and verify lease expiry/requeue recovers rather than losing the project.

## D. Optional external integrations

- [ ] Configure owner-authenticated yt-dlp cookies only if Instagram/TikTok source recovery needs them.
- [ ] Add `GEMINI_API_KEY` only if AI reranking/hooks/B-roll planning are desired; confirm local fallback still works without it.
- [ ] Install `requirements-ai.txt` and choose a Diffusers model only if local generated imagery is desired.
- [ ] Add Pexels/Pixabay API keys only for stock-video search sources you want enabled.
- [ ] Configure Upload-Post only after edit/export reliability is proven.
- [ ] Publish to private/test destinations first and verify idempotency before enabling production accounts.

---

# P0 — product features to build next

## 1. Interactive timeline + visual edit-plan editor

- [ ] Browser timeline with waveform, transcript, clip handles, B-roll regions, punch-in markers, caption regions, and music track.
- [ ] Drag in/out points with word/sentence snapping.
- [ ] Enable/disable clips and aspect-ratio variants without deleting history.
- [ ] Edit hook text, caption preset, B-roll layout, logo, music level, and punch-ins per clip.
- [ ] `Preview` and `Final render` actions that rerender without retranscription.
- [ ] Autosave plan revisions with explicit saved/rendering/error state.

**Acceptance:** a phone or desktop browser can alter a clip and rerender it without editing JSON or rerunning transcription.

## 2. Transcript correction + timing repair

- [ ] Edit transcript text in the project UI.
- [ ] Preserve timing for untouched text and realign only the modified sentence/region.
- [ ] Find/replace for names, products, jargon, and recurring transcription mistakes.
- [ ] Project/brand vocabulary hints.
- [ ] Surface low-confidence words where confidence metadata is available.
- [ ] Export corrected SRT, VTT, ASS, and plain transcript files.

**Acceptance:** correcting one word does not require a full retranscription and all captions/exports use the correction.

## 3. Proxy/preview rendering

- [ ] Generate reusable 540p/720p proxies for every source/camera.
- [ ] Use proxies for trim, caption, hook, B-roll, and layout previews.
- [ ] Cache previews independently from delivery renders.
- [ ] Mark preview state in the UI; never burn a draft marker into final delivery.
- [ ] Always return to the original/full-resolution source for final render.

**Acceptance:** common edit iterations preview in seconds without reducing final quality.

## 4. Context-aware B-roll engine + motion treatment

### Automatic semantic placement

- [x] Derive candidate visual moments from the exact words/phrases being spoken.
- [x] Avoid carpeting the entire clip with B-roll; cap cue density by clip length.
- [x] Snap the selected cue back to word timestamps immediately before FFmpeg render.
- [x] Automatically insert the resolved media over the phrase it illustrates.
- [x] Keep visual cues non-overlapping by default.
- [x] Preserve a `none` outcome when no configured provider returns an acceptable visual.

### Multiple B-roll loading paths

- [x] **Personal local library** — search reusable owned media by filename, folder context, and optional JSON tags/title/description.
- [x] **Pexels stock video** — optional API-key source with cached search and practical-resolution video selection.
- [x] **Pixabay stock video** — optional API-key source with cached safe-search results and video selection.
- [x] **Wikimedia Commons** — no-key reusable still-image fallback with attribution metadata.
- [x] **Local Diffusers** — optional locally generated image fallback when a configured model is available.
- [ ] **Manual upload per cue** from desktop/mobile.
- [ ] **Project asset bin** for media uploaded once and reused across clips in the same project.
- [ ] **Personal library UI** to tag, favorite, search, and blacklist reusable assets.
- [ ] **Direct licensed media URL** ingestion with explicit provenance/license fields.

### Resolver behavior and provenance

- [x] Deterministic provider waterfall configurable with `BROLL_PROVIDERS`.
- [x] Skip providers that are not configured instead of throwing setup errors.
- [x] Never overwrite a manual cue asset with an automatic result.
- [x] Keep provider, source URL, attribution, asset type, and relevance score on each cue.
- [x] Cache remote searches and stock downloads outside individual projects.
- [x] Hard-link/copy the selected asset into the project so the edit remains reproducible.
- [x] Bound remote download size and use atomic partial-file writes.
- [x] Ignore B-roll audio; creator speech/music remains authoritative.
- [x] Loop short video B-roll only for the required cue window.
- [x] Reject exact asset reuse within the same clip.
- [ ] Add embedding-based semantic reranking after provider search.
- [ ] Add visual quality scoring for blur, watermarks, severe compression, bad aspect, and unsafe/irrelevant content.
- [ ] Add perceptual duplicate detection so neighboring cues do not reuse near-identical shots.
- [ ] Add provider/license credit UI and downloadable attribution report.

### Motion and editor controls

- [ ] Deterministic Ken Burns pan/zoom for stills.
- [ ] Optional subtle parallax treatment where source geometry supports it.
- [ ] Conservative entrance/exit transitions.
- [ ] Replace/disable/reorder a cue directly on the timeline.
- [ ] Lock a selected B-roll asset so later rerenders cannot auto-replace it.
- [ ] Per-cue override for split, PIP, interruption, or no insert.
- [ ] B-roll A/B preview without rebuilding unrelated stages.

**Acceptance:** the user can speak about multiple concrete topics in one clip and Clipper automatically chooses contextually related media from multiple sources, places each item over the matching spoken phrase, preserves provenance, avoids unnecessary repeat downloads, and allows every automatic choice to be overridden.

## 5. Speaker diarization + active-speaker framing

- [ ] Stable speaker IDs across a source.
- [ ] User labels for speaker names.
- [ ] Associate speakers with face tracks/cameras when confidence is sufficient.
- [ ] Drive camera choice, crop target, split screen, and captions from active speaker.
- [ ] Conservative fallback when identity is uncertain.
- [ ] Minimum shot duration/hysteresis to prevent ping-pong cuts.

**Acceptance:** a two-person interview follows the active speaker without frantic cuts on brief interjections.

## 6. Advanced speech cleanup + mastering

- [ ] Optional denoise before loudness normalization.
- [ ] High-pass/EQ, gentle compression, de-essing, limiter/true-peak protection, optional de-reverb.
- [ ] Measure clipping, noise floor, loudness, and speech intelligibility before choosing processing strength.
- [ ] Keep original audio untouched and make processing reversible.
- [ ] Platform delivery presets around LUFS/true peak.
- [ ] A/B audio preview.

## 7. Platform safe-zone compositor

- [ ] Safe-zone templates for TikTok, Reels, Shorts, and other enabled targets.
- [ ] Simulated platform UI overlays in preview only.
- [ ] Platform-specific caption/hook/logo positions.
- [ ] Warnings when faces or important B-roll fall under known UI areas.
- [ ] Save target-specific layout overrides in the edit plan.

## 8. Publishing scheduler + durable retry dashboard

- [ ] Per-platform scheduled times/time zones.
- [ ] Durable publish state machine independent from render completion.
- [ ] Retry/backoff only for retryable failures.
- [ ] Token/account health warnings.
- [ ] Idempotency across worker restarts.
- [ ] Retry one platform without reposting successful targets.
- [ ] Save external post IDs/URLs returned by the provider.

## 9. Analytics feedback loop for personalized ranking

- [ ] Import available views, watch time, completion, rewatches, likes, comments, saves, shares, follows/conversions.
- [ ] Store the exact edit-plan/render signature for every published variant.
- [ ] Compare results by hook, score dimensions, duration, captions, B-roll density, layout, topic, and platform.
- [ ] Learn creator-specific ranking weights only after enough data exists.
- [ ] Keep generic baseline scoring visible.
- [ ] Prefer retention/engagement over vanity views when available.

## 10. Revision history + compare/rollback

- [ ] Immutable edit-plan revisions with parent IDs.
- [ ] Record who/what changed trims, captions, B-roll, hook, music, and layouts.
- [ ] Associate outputs with exact revision/signature.
- [ ] Side-by-side revision compare.
- [ ] Restore an old revision without deleting newer work.
- [ ] Never garbage-collect a revision referenced by a published post.

## 11. Shot-quality-aware multicam director

- [ ] Score blur/focus, exposure, face visibility, occlusion, shake, framing, and continuity.
- [ ] Prefer active speaker only when that camera passes a quality floor.
- [ ] Use reactions/wides intentionally at idea boundaries.
- [ ] Penalize excessive/discontinuous cuts.
- [ ] Expose automatic director decisions for manual override.

## 12. Thumbnail/title studio

- [ ] Rank frames by face visibility, expression, sharpness, and composition.
- [ ] Manual frame scrub/selection.
- [ ] Optional brand text treatment.
- [ ] Multiple truthful title/hook variants derived from the clip transcript.
- [ ] Platform-specific thumbnail variants where supported.
- [ ] Track the exact packaging variant published.

---

# P1 — production/workflow features

## 13. Translation + localization

- [ ] Translate corrected transcripts while preserving meaning.
- [ ] Bilingual subtitle modes.
- [ ] Locale-safe fonts and line breaking.
- [ ] Optional explicitly approved dubbed tracks.
- [ ] Localized caption sidecars and burned-in variants.

## 14. Batch ingest + watch folders

- [ ] Queue multiple files/projects from the UI.
- [ ] Local watch-folder profiles.
- [ ] Content-based source deduplication.
- [ ] Queue priority, pause/resume/cancel, overnight mode.
- [ ] One failed project cannot block later jobs.

## 15. Windows tray/service worker

- [ ] Auto-starting Windows service/tray app.
- [ ] GPU, queue, active job, disk use, last error, Firebase state.
- [ ] Graceful shutdown with consistent manifests/cache.
- [ ] Safe update + rollback.
- [ ] Keep the Actions runner lifecycle separate from the editing worker.

## 16. Storage lifecycle + deduplication

- [ ] Content-addressable cache for source/proxy/B-roll artifacts.
- [ ] Retention policies for Firebase inbox, proxies, prepared intermediates, old renders.
- [ ] Never delete originals/published revision artifacts without explicit policy.
- [ ] Local/Firebase storage usage dashboard.
- [ ] Dry-run cleanup report before destructive cleanup.

## 17. End-to-end benchmark suite

- [ ] Legally redistributable fixture set: talking head, fillers/silence, noisy audio, two speakers, multicam drift, portrait/landscape, silent video.
- [ ] Measure CPU/GPU time, VRAM, output size, WER sample, sync error, crop stability, failures, and render validity.
- [ ] Store deterministic baselines and gate meaningful regressions.
- [ ] Local A4000 benchmark command separate from every-push CI.
- [ ] Machine-readable JSON plus human report.

## 18. Windows installer + first-run diagnostics

- [ ] One-command/installer setup.
- [ ] Detect missing FFmpeg, NVENC, CUDA/CTranslate2, Firebase credentials, workdir permissions, network.
- [ ] Guided first-run diagnostics instead of raw exceptions.
- [ ] Credential/private-media-safe diagnostics bundle.

## 19. LAN/API security hardening

- [ ] Authentication if FastAPI is reachable beyond localhost.
- [x] Serve only explicitly allowed finished project media; do not mount the complete work directory.
- [x] Bound local upload byte size and stage uploads atomically.
- [ ] Validate uploaded media types/content with probing before expensive work.
- [ ] Appropriate request/job rate limits if the API is exposed beyond localhost.
- [x] Prevent browser clients from overwriting worker-produced Firebase project outputs.
- [ ] Evaluate Firebase App Check for the hosted uploader.
- [ ] Audit trail for destructive/project/publishing actions.

## 20. Offline-first PWA + resilient uploads

- [ ] Installable PWA.
- [ ] Local queue metadata while offline.
- [ ] Resumable large-recording uploads across browser/network interruptions.
- [ ] Resume without re-uploading completed objects.
- [ ] Distinct `uploaded`, `queued`, `claimed`, `processing`, `uploading results`, `publishing`, `done` states.

---

# Production acceptance matrix

## Correctness

- [ ] No clip references transcript words outside its rendered timeline.
- [ ] Smart cuts never restore a region selected for deletion.
- [ ] Captions remain synchronized after smart cuts, punch-ins, sync, B-roll, and rerenders.
- [ ] Separate mic/camera drift stays acceptably synchronized across a long real recording.
- [ ] Changing source/trim/style/logo/music/B-roll invalidates only dependent stages.
- [ ] Interrupted jobs recover without duplicate/corrupt output.

## Visual quality

- [ ] Subject tracking is stable with one face, multiple faces, brief face loss, and fast movement.
- [ ] 9:16, 4:5, 1:1, and 16:9 are manually reviewed on representative clips.
- [ ] Captions/hooks/logos stay in target safe zones.
- [ ] Context-aware B-roll appears over the correct spoken idea.
- [ ] B-roll provenance/license metadata is retained.
- [ ] No neighboring cues use distracting duplicate shots.

## Audio quality

- [ ] Headphones and phone-speaker checks pass without clipping, pumping, gating, or obvious sync error.
- [ ] Music never masks speech.
- [ ] B-roll source audio never leaks into final creator audio.

## Performance

- [ ] A4000 CUDA transcription confirmed in a real job.
- [ ] NVENC confirmed in a real final render.
- [ ] Cache-hit rerenders skip transcription, unchanged B-roll search/download/generation, and unchanged final encodes.
- [ ] Proxy preview is measurably faster than final render once implemented.
- [ ] Local/Firebase temporary storage remains bounded.

## Security/reliability

- [ ] Secrets stay outside Git and logs.
- [ ] Firebase isolation tested with two accounts.
- [ ] Self-hosted Actions never execute untrusted PR code.
- [ ] Local API is localhost-only or authenticated on LAN.
- [ ] Publishing retries cannot duplicate successful posts.
- [ ] Project plan + transcript + source reference backup/restore is proven.

## Definition of done

Clipper is ready for daily production use when one real recording can be uploaded from the phone, processed on the A4000 workstation, edited non-destructively from the browser, automatically illustrated with context-aware B-roll, rerendered quickly, reviewed in target-platform previews, downloaded/published, recovered after a forced interruption, and reproduced later from its exact saved revision — with hosted CI and the real A4000 smoke lane green.
