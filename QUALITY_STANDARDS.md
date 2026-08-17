# Clipper engineering quality standards

This repository should behave like a maintainable media pipeline, not a collection of demo scripts.

## Architecture

- Keep ingest, analysis, asset resolution, timing, rendering, synchronization, persistence, and publishing as separate modules.
- Provider/API-specific logic belongs behind a small interface; it must not leak into FFmpeg composition or project state handling.
- Expensive stages require deterministic cache keys and explicit invalidation dependencies.
- Project outputs must be reproducible from project-contained source references, transcript, edit plan, and selected assets.
- Automatic decisions must remain inspectable and overridable.

## Failure behavior

- Fail fast for required local runtime dependencies and malformed project state.
- Degrade gracefully for optional enrichment such as B-roll providers or Gemini.
- Never accept an incomplete artifact merely because a path exists.
- Use atomic writes for cache metadata, downloaded assets, manifests, and other state that a crash could corrupt.
- Bound remote downloads and timeouts.

## Media quality

- Preserve speech as the authoritative audio track; auxiliary/B-roll media must not leak audio into final output.
- Avoid unnecessary decode/encode passes and repeated loudness normalization.
- Tie transcript-aware edits to timestamped words rather than approximate prose positions whenever possible.
- Keep output compatible with common web/social playback: H.264/AAC, `yuv420p`, faststart, and explicit aspect presets.

## Tests

Every regression fix should include the smallest deterministic test that would have failed before the fix. CI should cover:

- pure/unit logic;
- import/runtime dependency compatibility;
- browser JavaScript parsing;
- real FFmpeg filters and synthetic render paths;
- optional real A4000 CUDA/NVENC validation on the trusted self-hosted workstation.

A feature is not considered complete because code compiles. The relevant acceptance criteria in `NEXT_IMPLEMENTATION_CHECKLIST.md` must be exercised with real media before production sign-off.
