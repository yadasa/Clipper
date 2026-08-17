# Clipper V2 — 10 must-have features

This pass adds ten creator-facing features that turn the automated pipeline into a practical daily editor rather than a one-shot clip generator.

1. **Non-destructive edit plans + rerender** — every project gets an editable JSON plan so trims, clip enable/disable state, layouts, caption style, hook text, brand kit, music, and ratios can be changed without retranscribing the source.
2. **Silence + filler-word smart cuts** — long pauses are tightened and obvious filler words can be removed when there is enough surrounding pause to make the cut sound natural.
3. **Active-word captions + caption presets** — karaoke-style highlighted active words with safe-zone-aware presets instead of static four-word subtitle chunks only.
4. **Automatic punch-ins / emphasis zooms** — transcript emphasis creates short zoom events so talking-head clips have visual motion without random cuts.
5. **Hook/title overlays** — a short on-screen hook is generated from the selected clip and shown in the opening seconds, with an optional Gemini rewrite when configured.
6. **Brand kits** — reusable JSON brand presets for font, text colors, accent color, logo, caption preset, and hook styling.
7. **Background music + speech ducking** — optional music beds are looped underneath a clip, ducked under speech, and mixed into the final loudness-normalized output.
8. **Multi-dimensional clip scoring + topic diversity** — candidate ranking now exposes hook, clarity, specificity, payoff, pace, and completeness subscores and avoids choosing several clips about the same idea.
9. **Social metadata + thumbnail extraction** — each finished clip gets generated title/caption/hashtags plus a representative thumbnail image for the project library and publishing layer.
10. **A4000-aware execution + stage cache/resume** — the local worker can auto-detect an NVIDIA RTX A4000-class machine, choose GPU-friendly Whisper/NVENC defaults, cache expensive stage outputs, and safely reuse completed work after interruption.

Every feature in this document has a regression-test target. The GitHub Actions workflow runs a fast quality gate and a real FFmpeg/runtime smoke test on every push; an optional self-hosted A4000 job additionally exercises CUDA and NVENC when the repository variable `A4000_RUNNER_ENABLED=true` and a runner labeled `self-hosted, windows, x64, a4000` is online.
