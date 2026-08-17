# services/pipeline — the end-to-end job runner

A real folder goes in. A searchable, deduplicated, analysed library comes out,
plus whatever finished artifacts this machine can actually produce.

```
folder
  -> workers/ingest (Rust)      BLAKE3, EXIF, pHash, 512px proxies
  -> packages/media-db          store, FTS index, vector index, job table
  -> analysis                   classical quality locally; SigLIP and SCRFD
                                through workers/ml-runtime over local gRPC
  -> packages/ranking-engine    score fusion, near-duplicate grouping, primaries
  -> packages/album-engine      event clustering, selection, layout, print gate
  -> workers/render-print       PDF/X-4

folder (video)
  -> workers/ingest             generate_video_proxy: 480p + frame index
  -> workers/video-analysis     visual, audio and shot producers -> FeatureStream
  -> packages/story-engine      plan_moments -> MomentRecords -> plan_reel -> EDL
  -> workers/render-video       EDL -> an .mp4
```

It generalises `scripts/demo/run_demo.py`, which already did folder → ingest →
media-db → dedupe. What the demo did not need, and a product cannot do without,
is knowing whether the work has already been done, and refusing to pretend when
it cannot be.

## Running it

```bash
python -m memory_engine_pipeline ~/Pictures/Thailand --workdir ~/.memory-engine/thailand
```

Prerequisites, each of which the runner reports precisely rather than
discovering halfway through:

| what | how | if missing |
|---|---|---|
| ingest worker | `cd workers/ingest && cargo build --release` | ingest → `unavailable` |
| model host | run `workers/ml-runtime`; point `--ml-runtime` or `$MEMORY_ENGINE_ML_RUNTIME` at it | analysis → `blocked` |
| print renderer | `cd workers/render-print && npm install && npm run build` | render-print → `unavailable` |
| video renderer | `cd workers/render-video && npm install && npm run build` | render-video → `unavailable` |
| ffmpeg / ffprobe | on `PATH` | story, render-video → `unavailable` |

Useful flags: `--stages ingest,analysis`, `--stages story,render-video`,
`--rescan`, `--icc-profile PATH`, `--album-photos N`, `--reel-seconds N`,
`--quiet`.

The run summary ends with a `produced` block listing every artifact by the stage
that wrote it, or the words "produced nothing" — a summary that lists seven
stages and no files reads as success to anyone skimming it.

Exit codes are the contract with a caller:

* **0** — every requested stage completed or had nothing to do
* **1** — something was `blocked` or `unavailable`. Recoverable by acting. **Not
  a success**, which is the point: a run that imported 40,000 photos and
  analysed none of them exits 1.
* **2** — something failed.

## The five stage outcomes

A stage that could not run must not report what a stage that succeeded reports,
and neither may report what a stage with nothing to do reports. Collapsing
those three is how a pipeline produces a confident, empty result.

| status | meaning | dependents run? |
|---|---|---|
| `completed` | did work, durably | yes |
| `skipped` | nothing to do; last run's output still valid | yes |
| `blocked` | a prerequisite a person must start (the model host) | no |
| `unavailable` | a worker that is not built | no |
| `failed` | ran and broke | no |

## Idempotence

Every stage builds a `JobSpec` whose `job_id` is a BLAKE3 over its identity
tuple, looks it up, and does nothing when it finds a completed one.

Ingest is the exception that needs extra machinery, because content addressing
cannot short-circuit the step that computes the content hash. It keeps a **stat
inventory** — path, size, mtime, one `stat` per file, no bytes read — written
only after a scan job reaches `completed`. Unchanged folder: the Rust worker is
never started. One file added: a delta scan whose source roots are the changed
files themselves, so N new files cost N files of work.

The inventory is a change *detector*, not an oracle: a file edited within the
filesystem's mtime granularity, to exactly the same length, is not detected.
That is the bet rsync makes. `--rescan` is for anyone who does not want to make
it, and it genuinely re-reads (a full-scan reopen, not a delta naming every
file as its own root).

**Aggregate stages are deliberately not proportional to the delta.** Dedupe,
ranking and album planning are functions of the whole library: adding a photo
can change which of a pair is the primary and which 24 photos make the book, so
recomputing is correct rather than wasteful. They read stored facts and open no
source files.

## Resumption

Killed `running` jobs are reclaimed to `pending` on startup — without that, a
crashed job stays `running` forever, the next run declines to touch it, and
reports success over a job nobody is executing.

* **ingest** resumes from the Rust worker's own path cursor. The worker rewrites
  its checkpoint atomically after every file, so that file is the authority on
  restart: the database copy is only synchronised when the subprocess returns,
  and a kill lands between those two moments by definition.
* **analysis** resumes from per-record, per-step state on the MediaRecords
  (`processing.stages.<step>.status == "done"`). Deliberately not
  `checkpoint.completed_input_ids`: a 300,000-element array rewritten after
  every image is quadratic, and it would be a second answer to "is this photo
  done?" that can disagree with the record.

Both are tested by killing things: `tests/test_resume_after_kill.py` SIGKILLs
the whole process group mid-scan and asserts the next run reports
`resumed_skips > 0` and `processed < total`, then compares the resumed library
field-for-field against one built in a single clean run.

## The model-host gate (issue #42)

Ingest, perceptual dedupe and classical quality all succeed with no model
loaded. So a pipeline whose model host was never running still produces a
library with thumbnails, dates, hashes, duplicate groups and quality scores —
everything a grid view renders. If the album stage then builds a book out of
it, the output is indistinguishable from a real one to everybody except the
person who receives the printed thing.

So when the host is absent, not serving, missing a model, or unable to load
one:

1. classical quality still runs (it needs nothing),
2. the model steps are left un-run and **unmarked** — not skipped, not zero,
3. the stage returns `blocked`, naming the host and the models,
4. no record reaches `processing.state == "analyzed"`,
5. the album stage refuses, naming analysis as the blocker,
6. the process exits non-zero.

There is no flag that downgrades this to a warning.

Issue #42 also said `classical_quality` had no executor, no formula and no
version pin, so a host could not produce the `quality.sharpness` and
`.exposure` the contract marks required. `memory_engine_pipeline/classical.py`
is that executor: four measures and two flags from the 512px proxy, no model,
no GPU, pinned at `classical-quality 1.0.0`. Its constants are priors, tunable
in one place; changing any of them changes the version, because scores from two
calibrations are not comparable.

## What is wired, and what is not

**Wired end to end and exercised by tests:** ingest → media-db → classical
quality → image embedding → face detection → fusion → dedupe → event clustering
→ selection → layout → print validation → AlbumSpec → PDF/X-4.

**Not wired:**

* **Face identity.** `cluster_faces` is not run, so no `FaceRecord` is written
  and no `person_id` is ever assigned. The MediaRecord carries a face COUNT with
  an empty `face_ids` list, which the album engine uses for people/scenery
  balance and which never claims to know who is in the frame. Face-safe layout
  is therefore working with an empty face set: the validator's face checks pass
  vacuously, which is honest (no face is *known* to be in the trim zone) and is
  not the same as "checked and safe".
* **Video, partly.** The chain 480p proxy → FeatureStream → `plan_moments` →
  `plan_reel` → `render-video` runs end to end and produces a playable `.mp4`.
  What is measured is measured; what is not is None all the way down, and
  `plan_moments` renormalises the absences rather than reading them as zero.
  Four producers are still missing and the stage names all four on every run:

  | missing | consequence |
  |---|---|
  | transcription (faster-whisper) | word timings unknown, so **no cut is certified word-safe** and the EDL carries no `no_mid_word_cut` finding at all |
  | face / smile detection (SCRFD) | `face_presence`, `max_face_area_ratio`, `smile_intensity` absent; the reel cannot prefer a face |
  | audio events (CLAP) | `speech` and `noise` ratios absent; the wind-noise and duck-under-speech rules can never fire |
  | beat detection | `music/library.json` bundles no audio, so there is no `BeatGrid` and **no cut in the reel is beat-locked** |

  The last two are the ones a reader will otherwise assume, so both are counted
  in the stage result (`beat_locked: 0`, `word_safe_cuts_certified: 0`) and
  printed in the summary line.

* **The film.** `packages/story-engine` has no film planner — the build plan
  puts it in phase 5 with story-arc prompting and speech-aware trimming. The
  story stage says so and plans only a reel. Relabelling a reel `kind: "film"`
  would cost one line and would be a lie about the product.
* **Tier 3.** No frontier-model call anywhere. Every JobSpec declares
  `requires_egress: false`.
* **Enhancement ops.** No restoration, upscale or spread-level colour harmony.

## Cross-boundary changes made here, which Codex must review

Three, all taken under issue #48, all **blocking defects the path in question
could not work around**. The third is in Claude's own package but it changes
what `workers/render-video` sees, so it belongs on this list:

1. **`workers/ingest/src/metadata.rs`** now reads `OffsetTimeOriginal` /
   `OffsetTimeDigitized` and resolves `captured_at.utc`. It previously left
   `utc` null unconditionally, even when the file carried an explicit offset —
   so every record was undated as far as `media-db`'s chronological listing and
   the album engine's clustering were concerned, and no album could ever be
   planned. `utc` is still never fabricated: no offset in the file, no `utc`.
   Rust unit tests cover signed offsets, a blank offset field (`"   :  "`, which
   is not UTC) and out-of-range values.

2. **`workers/render-print/src/gate.ts`** refused any AlbumSpec containing a
   check with `passed: false`, regardless of severity. The album engine emits
   warning-severity findings that do not pass — "300.3 DPI clears the floor but
   is below the vendor's preferred 350", and, on **both shipped vendor
   profiles**, "the vendor pins no `icc_hash`, so the profile was matched by
   name only". The second one is unconditional, so the gate refused *every*
   album the album engine can currently produce. The gate now blocks on
   error-severity findings, which is what the contract defines `pass` as
   (`error_count == 0` plus passing evidence for each hard gate). It is still a
   hard gate with no override.

3. **`packages/story-engine/memory_engine_story/reel.py`** emitted its
   `reframe_aspect_matches_target` and `reframe_keyframes_ordered` findings only
   when the plan carried a reframe track. `render-video` requires a PASSING
   finding for both before it renders anything, so **every plan whose source
   aspect already equals its target was unrenderable** — the ordinary 16:9
   master, and any reel with reframing off — and the renderer's complaint was
   about a missing check rather than about the absent crop that explained it.
   Both findings are now always emitted, with a detail that states the vacuity.
   `EdlValidation.checks` is the only place a consumer can distinguish "looked
   and found nothing wrong" from "never looked", so the fix is on the planner
   side rather than by relaxing the renderer.

## Known gaps, recorded rather than hidden

* **`MediaRecord.video` is never populated, so the source geometry is
  unmeasured.** `VideoProperties` (`oriented_size`, `rotation_deg`,
  `is_variable_frame_rate`) exists in the contract for exactly this and
  `workers/ingest` writes null. Two consequences, both taken rather than
  guessed around: the reel disables reframing (a landscape→vertical crop needs
  the source aspect ratio), and the render target is the **480p proxy raster**,
  which is the only picture geometry anything here has measured. A 1080p master
  needs the field filled in — an ingest change, not something to assume.
* **One timeline rate and one geometry per reel.** `render-video` refuses a
  source whose rate is not the timeline's rather than resampling it, so videos
  are grouped by (exact rate, proxy raster) and the largest group is planned.
  Everything else is reported as excluded with its rate and raster, never
  silently dropped.
* **The reel's ambient is planned at unity gain with no DSP.**
  `AmbientSettings`' defaults (−14 dB, a 120 Hz high-pass, `light` noise
  suppression) describe location sound sitting under a music bed; there is no
  bed, so the ambient is the whole mix. The high-pass and the suppression label
  are refused by `render-video` on contracts#53 in any case — the corner
  frequency is pinned and the filter response is not. When a music track exists,
  planning with the defaults is expected to be **refused**, and the stage
  reports the refusal with its issue numbers rather than dropping the field.
* **GoPro spans across a delta scan.** The Rust worker reconciles chapter
  assemblies across the outputs of ONE job, so a chaptered video whose later
  chapters arrive in a second scan is not re-assembled until `--rescan`. Fixing
  it belongs in `workers/ingest`.
* **`ModelRun` cannot record a config digest.** The host reports
  `config_blake3` on every response and the contract's `ModelRun` has nowhere to
  put it, so provenance records the weights hash only. Two runs with identical
  weights and a moved score threshold are indistinguishable in the record. This
  needs a `contracts/` change.
* **Development ICC substitution.** Without `--icc-profile`, the print stage
  asks the renderer for its built-in CMYK profile under the vendor's profile
  name. The renderer permits this only while the vendor profile pins no
  `icc_hash`; the runner announces it on every run. Do not send the result to a
  printer.
* **MediaRecords are not schema-validated at write time.** Validating 300,000
  records per run costs minutes. The end-to-end test validates every record the
  pipeline writes against `media-record.schema.json`, which catches drift in CI
  rather than per run.

## Tests

```bash
cd services/pipeline
find . -name __pycache__ -type d -exec rm -rf {} +
python3 -m unittest discover -s tests
```

44 tests, about a minute. They use the real Rust ingest binary on real JPEGs
with real EXIF, and a **fake model host that speaks real gRPC** over a loopback
socket using the generated stubs — a fake MODEL, not a fake transport, so every
message shape, error path and correlation rule is exercised for real. Set
`MEMORY_ENGINE_SLOW_TESTS=1` to add the PDF render (about four minutes:
twenty 306mm pages at 350 DPI).

## What happens today at 500GB

Honestly, and mostly untested at that scale — the largest library actually run
through this is thirty photos:

* **The walk** is one `stat` per file. Fine.
* **Ingest** is the wall: every file is read once for BLAKE3, decoded for pHash
  and a thumbnail, single-threaded in the Rust worker with no parallelism the
  runner can ask for. Disk-bound at best, CPU-bound in practice on JPEG decode.
  Progress is real and the kill/resume path is tested, so it can be run
  overnight and interrupted — but it will take hours, and nothing here has
  measured how many.
* **Analysis** batches 32 items per gRPC call and holds one record in memory at
  a time. The throughput ceiling is the model host's, not this runner's.
* **Ranking** loads every record into memory to fuse scores — roughly 200k
  records before that becomes uncomfortable, and it is a `list()` that should
  become a stream.
* **Album planning** is bounded by one event cluster, so library size barely
  matters.
* **Print rendering** is minutes per book regardless of library size.

The failure mode to expect first is ranking's full-library `list()`, and the
one to expect second is ingest throughput. Neither is a correctness problem;
both would need measuring on a real drive before any claim about them is worth
anything.
