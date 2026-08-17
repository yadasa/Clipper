# Clipper Auto Mode

Auto Mode is the default one-click workflow for raw creator recordings. The goal is not to turn every checkbox on; it is to make each decision at the stage where it has the best information, establish one authoritative timeline early, and avoid doing expensive work twice.

## The pipeline

```text
raw video + cameras + mic
        |
        v
1. ingest + validate
        |
        v
2. sync / precomp
   - inspect primary + separate mic audio
   - choose the usable authoritative audio track
   - waveform sync first
   - transcript-refine weak sync estimates only when needed
   - estimate camera offset/drift
        |
        v
3. authoritative precomp transcription
   - faster-whisper word timestamps
   - transcript now describes the synced edit timeline
        |
        v
4. global cleanup master
   - remove safe dead air / reliable filler cuts once
   - remap every word timestamp once
   - save clean-master EDL
        |
        v
5. coherent story selection
   - detect spoken idea units
   - adapt target clip duration/count to speaking pace + source length
   - score hook/clarity/payoff/pace/completeness/coherence
   - optionally stitch chronological related slices while skipping irrelevant speech
        |
        v
6. automatic edit decisions
   - choose visual density per clip
   - choose B-roll count/layout
   - choose punch-in density
   - choose hook treatment
   - choose caption preset based on delivery pace
        |
        v
7. visual + audio composition
   - smart reframing / multicam master
   - B-roll, PIP/split/interruption
   - logo / hook
   - music ducking + delivery audio
        |
        v
8. captions LAST in the visual graph
   - captions are composited after crop, B-roll, logo, hook and overlays
   - still one final encode; no unnecessary second-generation transcode
        |
        v
9. delivery QA
   - dimensions / duration / streams / audio / pixel format
   - retry a failed complex render with a simpler visual graph
        |
        v
10. thumbnails + social metadata + project library
```

Every run writes `auto_stages.json` so the pipeline is inspectable instead of being one opaque “processing” operation. Firebase jobs upload the report as `automation_report.json` beside the project edit plan.

## Why this order

### Sync before the authoritative transcript

Clip selection and captions should describe the audio/video combination that will actually be rendered. Auto Mode therefore does a cheap waveform sync first, creates the authoritative precomp, and transcribes that timeline. Transcript-based sync is reserved for weak waveform matches so Clipper does not transcribe every raw track unnecessarily.

### Clean before selecting clips

Dead-air cleanup changes time. If each selected clip independently removes pauses, the same source moment can have different downstream timestamps depending on which candidate it belongs to. Auto Mode instead creates one clean master, remaps the transcript once, and performs story selection against that canonical timeline.

### Select stories before decorating them

B-roll, hooks, motion, music and captions are downstream presentation decisions. They should never influence whether the spoken content itself is coherent. Auto Mode first chooses the strongest story, then decides how much visual treatment that story needs.

### Captions last

Captions need to sit above the final visual composition and use the final remapped timestamps. In `clipper/render.py` subtitles remain the final visual filter after crop/reframe, B-roll, logos and hooks. That gives the requested “captions last” behavior without forcing a second lossy encode.

## Seven additional automatic features implemented with Auto Mode

### 1. Automatic best-audio selection

When a separate mic is supplied, Clipper measures signal level, clipping and activity. The separate mic is preferred unless it appears materially broken or worse than the camera reference audio. The decision is recorded in the stage report.

### 2. Hybrid sync escalation

Raw tracks use waveform correlation first because it is cheap. Low-confidence estimates escalate to word/transcript anchors and robust drift fitting. This spends transcription/GPU time only when the cheaper synchronization signal is ambiguous.

### 3. One global clean master

Silence tightening and reliable `um/uh` cleanup happen once before clip selection. The resulting EDL and remapped transcript are saved, making every later stage refer to the same timeline.

### 4. Adaptive clip duration and clip count

There is no fixed “make everything 30 seconds” rule. Speaking rate and source duration influence how long a self-contained clip should be and how many genuinely different opportunities should be returned.

### 5. Coherent multi-slice story stitching

Auto Mode can combine a small number of chronological, semantically related source slices while skipping an unrelated section between them. It never reorders speech. The source ranges are first-class edit-plan data, so the result remains non-destructive and inspectable.

### 6. Adaptive edit intensity

Fast, information-dense speech gets less visual noise; concrete/specific speech can receive more contextual B-roll. Auto Mode can reduce punch-ins, choose a cleaner caption preset and vary B-roll density per clip rather than applying one global maximal style.

### 7. Automated delivery QA + safe fallback

Finished variants are probed for expected dimensions, approximate duration, video/audio streams and delivery pixel format. If the complex visual graph fails QA, Clipper retries the same edit with optional B-roll removed before failing the job. QA reports are saved under `qa/`.

## Auto versus Manual

`AUTOMATION_MODE=auto` is the default. The web uploader exposes **Auto** and **Manual** modes, the local API accepts `automation_mode=auto|manual`, and the CLI accepts `--mode auto|manual`.

Manual mode preserves the explicit edit switches and the older direct path. Auto Mode uses those settings as high-level allow/deny controls, but it decides when and how strongly to apply them.

## Tuning

```env
AUTOMATION_MODE=auto
AUTO_GLOBAL_CLEANUP=1
AUTO_STORY_STITCH=1
AUTO_VISUAL_INTENSITY=1
AUTO_QUALITY_GATE=1
AUTO_SYNC_REFINE_CONFIDENCE=0.24
AUTO_CLEANUP_MAX_REMOVED_RATIO=0.58
AUTO_MIN_CLIP_SECONDS=15
AUTO_MAX_CLIP_SECONDS=55
```

The defaults are deliberately conservative. The first real workstation benchmark should measure both quality and speed before making the pipeline more aggressive.