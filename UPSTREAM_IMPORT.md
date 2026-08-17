# OpenShorts import record

Upstream: `mutonby/openshorts`

Target: `yadasa/Clipper`

Import date: 2026-08-16

## What was inspected

The upstream repository tree, root README, root license, `cloud/LICENSE`, dependency list, core clipping pipeline, clip-selection helpers, FFmpeg encoder helpers, and the FFmpeg-native reframe implementation were reviewed before Clipper was built.

The useful upstream clipping architecture was treated as the baseline rather than blindly carrying every product surface into a personal workstation.

## License boundary

The repository root explicitly says that code outside `cloud/` is MIT-licensed. The upstream `cloud/LICENSE` separately says `cloud/` is not MIT and prohibits redistribution of that commercial source as part of another product without a separate agreement.

Because `yadasa/Clipper` is public, copying `cloud/` into it would violate that redistribution boundary. Therefore:

- `cloud/` source is **not** copied into Clipper.
- Firebase web/queue/worker functionality in Clipper is an independent implementation.
- Upstream commercial billing/metering/hosted-service code is not used.

This exception is also preserved in the root `LICENSE` file.

## Upstream code directly imported under MIT

### `ffmpeg_utils.py`

Imported as the common encoding layer and retained/adapted for:

- x264 fallback.
- NVIDIA NVENC probe and selection.
- delivery quality tiers.
- metadata/chapter scrubbing.
- AAC/loudness-normalization helpers.

### `clip_selection.py`

Imported as pure selection helpers and retained/adapted for:

- transcript windowing.
- target clip-count logic.
- compact word timing representation.
- snapping proposed cuts onto spoken-word edges.
- model-price lookup support.

### Root `LICENSE`

Copied so the upstream MIT notice and the explicit `cloud/` exception remain visible in the target repository.

### `upstream-requirements.txt`

A dependency snapshot is preserved for audit/comparison. Clipper's actual `requirements.txt` is narrower and reorganized around the local-first product.

## Upstream behavior reimplemented or redesigned

The following ideas were inspected upstream but implemented in Clipper's own focused modules rather than copied wholesale:

- low-resolution subject analysis + FFmpeg-native full-resolution reframe.
- smarter vertical composition.
- faster-whisper transcript pipeline.
- candidate clip selection/ranking.
- Firebase job handoff.
- web project library.
- social publishing adapter.

The resulting Clipper implementation is deliberately smaller than the full upstream platform because AI UGC actor generation, public gallery SEO pages, hosted billing, and YouTube thumbnail/title tooling are not required for the requested local clipping workflow.

## Files intentionally not mirrored

### `cloud/**`

Excluded because of the separate OpenShorts Commercial License and the public nature of this target repository.

### Large demo/example media

Excluded because binary demo GIF/video files are not required to run Clipper and would bloat a newly created repository.

### Product surfaces unrelated to the requested local clipping workstation

Not mirrored as active Clipper code:

- AI UGC actor/product-video generator.
- public avatar/video SEO gallery.
- hosted billing/metering surfaces.
- YouTube thumbnail/title studio.

Their omission is intentional scope reduction, not a missing clipping dependency.

## Functional replacement map

| Upstream concept | Clipper replacement |
|---|---|
| Clip moment detection | `clipper/analysis.py` + `clip_selection.py` |
| faster-whisper transcript | `clipper/transcription.py` |
| Smart reframe | `clipper/focus.py` + `clipper/render.py` |
| FFmpeg encoding | `ffmpeg_utils.py` + `clipper/render.py` |
| Social ingest | `clipper/media.py` |
| Social publishing | `clipper/publish.py` |
| Hosted job processing | `clipper/firebase_bridge.py` + `clipper/worker.py` |
| Browser UI | `web/` |
| User media storage | Firebase Storage layout in `clipper/firebase_bridge.py` |
| Results metadata | Firestore `clipperProjects` + local `manifest.json` |

## Why this approach is safer and more useful than a literal dump

A byte-for-byte dump would have created three problems:

1. It would redistribute separately licensed `cloud/` code into a public repository.
2. It would bring in product areas unrelated to the requested personal clipping workflow.
3. It would preserve upstream architecture assumptions instead of implementing the requested mobile-to-home-computer Firebase handoff, multicam sync, multi-ratio grouping, and transcript-aware imagery.

Clipper therefore preserves the redistributable baseline and attribution while replacing the hosted/commercial and out-of-scope pieces with a purpose-built local-first system.
