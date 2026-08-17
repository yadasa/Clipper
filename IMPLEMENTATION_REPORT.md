# Clipper implementation report

This file tracks the requested work in the order it was requested. Items are checked only when the implementation is present in `main` and the related validation pass has been completed.

## Ordered checklist

- [x] Inspect `mutonby/openshorts`, its architecture, capabilities, performance claims, dependencies, and licensing.
- [ ] Import the redistributable OpenShorts baseline into `yadasa/Clipper` while preserving upstream attribution and licensing.
- [ ] Document how the imported baseline works, its capabilities, speed characteristics, and improvement opportunities.
- [ ] Identify what is missing for a true local-first automated clipping workflow for recorded videos and multi-platform publishing.
- [ ] Implement the missing local-first automated clipping features.
- [ ] After each feature, run a focused bug/optimization pass and apply fixes.
- [ ] Implement transcript-aware visual/B-roll planning that finds or generates imagery aligned with what is being discussed at each moment.
- [ ] Implement multiple visual edit choices: split screen, picture-in-picture, and interrupt/full-screen inserts.
- [ ] Optimize visual compositions independently for selected aspect ratios.
- [ ] Apply a beige/brown UI theme with muted accent colors.
- [ ] Run another full optimization and bug-fix pass.
- [ ] Optimize upload for local-device media and pasted Instagram/TikTok links.
- [ ] For user-owned social content, prefer the highest-quality clean source exposed by the platform/extractor and keep an explicit ownership acknowledgement in the workflow.
- [ ] Add separate audio/video synchronization using timestamped speech, waveform correlation, and drift correction for multicam/separate-microphone recordings.
- [ ] Add a mobile/web ingest flow backed by Firebase Storage + Firestore.
- [ ] Add a local desktop worker that claims Firebase jobs, downloads source media, edits locally, and uploads results.
- [ ] Add a Firebase-hostable library UI organized by source video, with every finished version grouped under that source.
- [ ] Store and expose multiple selected aspect-ratio variants per finished clip.
- [ ] Allow finished videos to be watched and downloaded from the web UI.
- [ ] Add social-media-oriented export presets.
- [ ] Add publishing adapter hooks for supported social platforms/services without hard-coding credentials.
- [ ] Run a final bug pass.
- [ ] Search for and implement additional optimization opportunities.
- [ ] Re-audit this checklist against the repository and fix anything missing before declaring completion.

## Licensing note

The upstream root license is MIT except for `cloud/`, which is under the OpenShorts Commercial License. The target repository is public, so `cloud/` cannot be mirrored into it as redistributable source. Clipper therefore preserves the MIT-licensed baseline where practical and implements its Firebase/cloud workflow independently rather than copying OpenShorts commercial `cloud/` code. Large demo media assets are not required by the application and are intentionally not mirrored.

## Validation log

- 2026-08-16: Target repository verified writable and initially empty.
- 2026-08-16: Upstream license inspected. Root MIT license permits reuse; `cloud/` has separate restrictions and is excluded from public redistribution.
- 2026-08-16: Imported upstream MIT encoder-selection utility as the first baseline module.
