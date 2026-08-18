# workers/video-analysis — the producers behind `plan_moments`

`packages/story-engine`'s `plan_moments` turns a **FeatureStream** into
MomentRecords. Until this worker existed nothing produced one, so film and reel
— two of the three outputs this product promises — could not run at all, and
`services/pipeline`'s story stage reported `unavailable` and named the
producers that were missing.

This is those producers.

```
workers/ingest (Rust)        480p proxy + frame-index sidecar   [REUSED, not rebuilt]
        │
        ▼
visual.py   photometry, sharpness, motion, shake, novelty       classical, runs today
audio.py    K-weighted (BS.1770-4) loudness, RMS, silence       classical, runs today
shots.py    content-hysteresis shot detection                   classical, runs today
            TransNetV2                                          seam; weights missing
transcript.py  word timings                                     interface + null backend
        │
        ▼
stream.py   FeatureStream  ──►  plan_moments  ──►  MomentRecords
```

## Running it

Prerequisites, each of which is reported by name rather than discovered
halfway through:

| what | how | if missing |
|---|---|---|
| FFmpeg + FFprobe | any 6.x/7.x build | `decode.ToolMissing`, naming `MEMORY_ENGINE_FFMPEG` |
| a 480p proxy per video | `workers/ingest`'s `generate_video_proxy` job | that video is skipped and counted; the run exits 1 even when other videos succeed |
| `blake3` | proxy bytes and `moment_id` are content-addressed | analysis refuses proxy bytes that do not hash to their declared `proxy_id`; `plan_moments` never substitutes another hash |

The end-to-end run that produced moments from video, exactly as executed:

```bash
# 1. a synthetic library (no real photographs are ever needed)
MEMORY_ENGINE_FFMPEG=/opt/homebrew/bin/ffmpeg \
  python3 scripts/demo/make_library.py --out /tmp/vidlib --stills 40 --clips 10

# 2. folder -> MediaRecords (the Rust ingest worker, through the pipeline)
cd services/pipeline && python3 -m memory_engine_pipeline /tmp/vidlib \
  --workdir /tmp/vidwork --stages ingest

# 3. MediaRecords -> 480p proxies + frame indices (the Rust ingest worker)
#    A generate_video_proxy JobSpec built with
#    memory_engine_pipeline.jobstore.build_job over the video media_ids, then:
MEMORY_ENGINE_FFMPEG=/opt/homebrew/bin/ffmpeg \
  workers/ingest/target/release/memory-engine-ingest \
  /tmp/proxyjob.json /tmp/vidwork/records /tmp/proxyckpt.json

# 4. proxies -> MomentRecords   <- THIS WORKER
cd workers/video-analysis && \
MEMORY_ENGINE_FFMPEG=/opt/homebrew/bin/ffmpeg \
MEMORY_ENGINE_FFPROBE=/opt/homebrew/bin/ffprobe \
  python3 -m memory_engine_video_analysis /tmp/vidwork --at 2026-08-17T00:00:00+00:00
```

Exit codes follow `services/pipeline`'s vocabulary: **0** every requested video
produced moments; **1** nothing to analyse or a prerequisite a person must
supply; **2** analysis ran and broke. A run that finds forty videos and
analyses none of them exits 1. So does a partial run: one successful video
cannot hide another requested video's missing proxy.

Every run that reaches record selection writes
`<workdir>/video-analysis-report.json` (or `--report`) atomically. It records
the exit code and `discovered`, `analysed`, `skipped`, `failed`, and `deferred`
video counts, so automation never has to infer completeness from the presence
of some MomentRecord files.

## What is measured, and what is not

Absence is reported, never filled in. `moments.py` renormalises a missing
signal out of the fusion and reports the reduced coverage; a fabricated zero
would be a measurement nobody took, indistinguishable from a real one forever
after.

| signal | state |
|---|---|
| `luma`, `clipped_highlights`, `clipped_shadows` | measured |
| `sharpness` | measured, on the same 512px raster and with the same constant as `classical-quality 1.0.0`, so a frame of video and a still are comparable |
| `exposure_stability` | measured, over a 0.75s window |
| `motion`, `shake`, `novelty` | measured; the first frame has no motion and the first two no shake, and those are `None` |
| `loudness_lufs` | measured — real LUFS, K-weighted per BS.1770-4, cross-checked against FFmpeg's `ebur128` |
| shot boundaries | classical detector, see below |
| `face_presence`, `smile_intensity`, `max_face_area_ratio` | **NOT MEASURED** — SCRFD runs in `workers/ml-runtime` and this worker does not speak to the host |
| `speech`, `noise` | **NOT MEASURED** — needs a VAD / the CLAP event head |
| audio events (laughter, cheering…) | **NOT MEASURED** — CLAP, same reason |
| transcript / word timings | **NOT AVAILABLE** — no STT model is shipped and none is faked |

### The transcript, stated plainly

The null backend reports "no transcript available" with a reason.
`moments.py` handles that correctly: no speech snap points are certified, no
`TranscriptSegment` is written, and `safe_trim.speech_safe_in/out` stay null.

The honest consequence: **without a transcript the no-mid-word guarantee is
vacuous.** It is not violated — nothing claims a cut is speech-safe — but
nothing is checked either, and a moment planned from a null-transcript stream
may cut through a sentence. Wiring `faster-whisper` behind
`TranscriptBackend` is what closes it.

### Shot detection: which one ran

`models/registry.json` names **TransNetV2** and lists it as `required_for:
[moment_scoring, reel_planning]`. Its weights are not in this environment, so
the model load gate refuses it — `UNLOADABLE_REASON_WEIGHTS_MISSING`, in
release *and* development mode. That refusal is consulted through
`workers/ml-runtime`'s `ModelCatalog` (never reimplemented), recorded in every
`AnalysisReport`, and printed on every run.

What runs is `content-hysteresis 1.0.0`, and **it is the weaker of the two**.
Measured behaviour, all pinned in `tests/test_shots.py`:

| case | dE | result |
|---|---|---|
| hard cut (testsrc2 → smptebars) | 102.4 | found, at the exact frame |
| 0.4s cross dissolve | 10.2 | found, one boundary |
| **1.5s cross dissolve** | 3.2 | **missed — known limitation, pinned as such** |
| white flash frame | 56.6 | rejected as a flash |
| fast whip pan | 12.9 sustained | not a cut (the adaptive baseline is what saves it) |
| ten continuous demo clips | ≤2.9 | one shot each |

It is also weak on cuts between shots that look alike, and on anything
learned. A **missed** boundary is the expensive error — `_segments` in
`moments.py` uses shots to make "a moment never crosses a cut" structural, so a
missed one lets a moment span a scene change and reach a finished reel. The
constants prefer the cheap error, which is a false boundary.

To upgrade: implement `shots.ShotBackend` (it takes the proxy path, because
TransNetV2 consumes pixels rather than colour statistics) and pass
`backend=` to `detect_shots`. Nothing else changes.

## Calibration is a prior, not a truth

Every threshold in `visual.py` and `shots.py` is a hand-chosen constant with a
version pin. They have never been calibrated against real footage; the only
footage they have run on is `scripts/demo/make_library.py`'s synthetic clips,
whose own docstring says nothing tuned against them transfers.

This matters more here than in the photo path because `Policy` applies **hard
elimination gates** to these numbers — `dead_motion_max`, `shake_max`,
`blown_highlight_max` — and eliminated footage is never scored, ranked or
shown. The same hazard is recorded as issue #22 for `DEFAULT_SHARPNESS_FLOOR`.

One such defect was already found and fixed here by measurement rather than by
reading: the shake estimator originally searched **integer** shifts on a 64px
raster, a sub-pixel pan quantised to a displacement flipping between 0 and 1,
and every panning clip in the demo library was eliminated by `shake_max`.
Nothing raised. `tests/test_visual.py::test_a_smooth_pan_does_not_read_as_shake`
is that case.

## Performance, measured and honest

Measured on the ten demo-library clips on an M-series laptop: **1365 frames /
44.0 s of footage in 17.2 s**, i.e. 12.6 ms per frame, **2.55x realtime**,
decode and all producers included.

Extrapolated, a 200-hour library is about **78 hours** of analysis. That is far
outside the build plan's overnight target, and nothing here has been run at
that scale — the number above is 44 seconds of synthetic 480p, and it is an
extrapolation, not a measurement of a large library. The cost is the RGB decode
and the per-frame numpy reductions, both single-threaded, both trivially
parallel across files. No attempt at that has been made.

## Tests

```bash
cd workers/video-analysis
find . -name __pycache__ -type d -exec rm -rf {} +
python3 -m unittest discover -s tests
```

The full suite takes about a minute. **There are no skips.** Every test needs FFmpeg,
because every line of the production path is "get pixels or samples out of a
video", and a suite that skipped its way to green when the decoder is missing
would report a working video analyser on a machine that cannot open a video.
If FFmpeg is absent the suite is red.

The clips are generated by FFmpeg at test time and are hostile on purpose: a
hard cut, a flash, a whip pan, two dissolves, a smooth sub-pixel pan, a
silence→tone onset, a steady tone at a known level, a clip with no audio
stream, and pure black. `tests/fixtures/ingest-29-97.idx` is a frame-index
sidecar captured verbatim from a real run of the Rust ingest worker — the one
thing in the suite that pins the reader against the writer instead of against
another test helper.

## Not this worker's job

* **Making proxies.** `workers/ingest/src/video.rs` already generates the
  single-pass 480p proxy with hardware decode, the frame-index sidecar, atomic
  writes, a resumable checkpoint and GoPro span reconciliation. A second
  implementation would be a second answer to "what is the 480p raster of this
  video", and the difference would show up as a wrong source timecode.
* **Variable frame rate.** `FeatureStream` places sample i at source frame
  `start + i`, which assumes a uniform grid. VFR footage is REFUSED by name
  rather than resampled onto a grid it does not sit on. That is a seam.
* **Running the model host.** Faces, audio events and transcription all live
  there.
* **Wiring the pipeline.** `services/pipeline`'s story stage still reports
  `unavailable`, correctly: this worker exists, but that runner does not invoke
  it or the `generate_video_proxy` job yet.
