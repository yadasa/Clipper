# B-roll architecture

Clipper treats B-roll as a planned semantic edit decision followed by asset resolution. Timing and sourcing are separate concerns so provider-specific API behavior never leaks into the renderer.

## Flow

1. `clipper.analysis.plan_visual_cues()` turns the selected clip transcript into non-overlapping semantic cue windows.
2. `clipper.visuals.resolve_visuals()` delegates to `clipper.broll.resolve_broll()`.
3. The B-roll resolver walks the configured provider order and chooses the first sufficiently relevant asset.
4. The selected asset is materialized into the project and its provider/provenance metadata is stored on `VisualCue`.
5. `clipper.render.align_visual_cues()` matches the cue transcript back to word timestamps and snaps the visual window to the words it describes.
6. The renderer composes the asset as split-screen, PIP, or full-screen interruption.

This keeps cue planning deterministic and lets providers be added or removed without changing FFmpeg composition code.

## Provider waterfall

Default order:

```text
local -> pexels -> pixabay -> commons -> diffusers
```

Configure it with:

```env
BROLL_PROVIDERS=local,pexels,pixabay,commons,diffusers
```

Unknown or unavailable providers are skipped. `VISUAL_PROVIDER=none` disables automatic B-roll; the older `commons`, `diffusers`, and `auto` values remain supported for compatibility.

### Local library

`BROLL_LIBRARY` points at a directory containing reusable video or still assets. Search considers:

- filename
- the last directory components
- optional sidecar title
- optional description
- optional tags

Supported sidecars are `<asset.ext>.json` or `<asset>.json`. Useful keys include:

```json
{
  "title": "Coffee beans roasting in drum",
  "description": "Close-up of a small-batch coffee roaster",
  "tags": ["coffee", "beans", "roasting", "roaster"],
  "creator": "TJ",
  "license": "owned",
  "source_url": null
}
```

The library is indexed once per resolver instance. The selected file is hard-linked into the project when possible, otherwise copied.

### Pexels

Pexels is an optional stock-video source enabled by `PEXELS_API_KEY`. Clipper requests video search results, prefers practical delivery resolutions rather than blindly downloading 4K, caches the search response, downloads the selected MP4 into the shared B-roll cache, then materializes the file into the project.

Provider/source/creator metadata is retained on the cue.

### Pixabay

Pixabay is an optional stock-video source enabled by `PIXABAY_API_KEY`. Search uses safe-search and cached API responses. Clipper prefers the medium stream before larger files, retains tags/source/creator metadata, and materializes the selected video into the project.

### Wikimedia Commons

Commons remains the zero-key image fallback. Images are cached globally by query and attribution metadata stays beside the selected file.

### Local Diffusers

If `DIFFUSION_MODEL` is configured, Clipper can generate a still image locally as the final fallback. Generated assets are cached by model + prompt + generation settings.

## Timing

Planner timing is based on sentence/phrase structure. When actual word timestamps are available at render time, the renderer matches the cue transcript back to the clip words and snaps the cue to that phrase.

This prevents the common failure mode where an otherwise relevant stock shot appears several seconds before or after the sentence it is supposed to illustrate.

## Rendering video versus stills

`VisualCue.asset_type` is either `image` or `video`.

- Images are looped as still FFmpeg inputs.
- Videos are stream-looped and trimmed to the cue duration.
- B-roll audio is never mapped into the finished clip.
- Short stock clips can therefore cover a longer cue safely without disturbing speech/music.
- Existing split/PIP/interrupt composition rules apply to both media types.

## Caching and failure behavior

- Remote search payloads are cached under `data/cache/broll/search`.
- Downloaded stock videos are cached under `data/cache/broll/assets`.
- Commons and generated imagery use shared B-roll cache directories.
- Selected assets are copied/hard-linked into `data/projects/<project>/visuals/...` so a project remains reproducible.
- Downloads are bounded by `BROLL_MAX_DOWNLOAD_MB` and use atomic `.part` writes.
- A failed provider does not fail the edit; the resolver logs the failure and advances to the next configured source.
- A manual cue asset is never replaced by automatic resolution.

## Configuration

```env
BROLL_AUTO_INSERT=1
BROLL_PROVIDERS=local,pexels,pixabay,commons,diffusers
BROLL_LIBRARY=
BROLL_MAX_CUES=6
BROLL_MIN_RELEVANCE=0.30
BROLL_MAX_DOWNLOAD_MB=80
BROLL_SEARCH_CACHE_HOURS=24

PEXELS_API_KEY=
PIXABAY_API_KEY=

DIFFUSION_MODEL=
VISUAL_PROVIDER=auto
```

## Extension contract

New providers should implement the same small interface:

```python
class Provider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def resolve(
        self,
        cue: VisualCue,
        query: str,
        output_dir: Path,
    ) -> BrollAsset | None: ...
```

A provider must return project-usable media plus media type, provider name, source URL, attribution, and a relevance score. It should not modify render behavior or introduce provider-specific branches outside `clipper.broll`.

## Remaining work

The next high-value B-roll improvements are intentionally listed in `NEXT_IMPLEMENTATION_CHECKLIST.md`: visual embedding reranking, quality rejection, duplicate detection, timeline replace/disable controls, provider credit UI, personal library management, and deterministic motion treatment for stills.
