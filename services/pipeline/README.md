# services/pipeline — the end-to-end job runner

A real folder goes in. A searchable, deduplicated, analysed library comes out,
plus whatever finished artifacts this machine can actually produce.

```
folder
  -> workers/ingest (Rust)      BLAKE3, EXIF, pHash, 512px proxies
  -> packages/media-db          store, FTS index, vector index, job table
  -> analysis                   classical quality locally; SigLIP, SCRFD and
                                ArcFace through workers/ml-runtime over local
                                gRPC
  -> packages/face-identity     clustering, person assignment, review queue
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
`--rescan`, `--reanalyze-faces`, `--icc-profile PATH`, `--album-photos N`,
`--reel-seconds N`, `--quiet`.

The run summary ends with a `produced` block listing every artifact by the stage
that wrote it, or the words "produced nothing" — a summary that lists eight
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

## Faces

Three stages touch faces, and they are deliberately not one stage.

| step | where | what it produces | resumes |
|---|---|---|---|
| `face_detection` | analysis, per record | a FaceRecord per face: box, landmarks, detector pin, minor-safety envelope. No embedding, unassigned identity. | per record |
| `face_embedding` | analysis, per record | the host warps each face onto ArcFace's canonical five-point template and embeds it; the record gains a VectorRef into `arcface_buffalo_l_512` | per record |
| `faces` stage | whole library | clusters, assignments, `outputs/faces/review-queue.json`, and the face summary on each MediaRecord | not resumable, by nature — see below |

Detection and embedding are separate steps because they fail separately: a
detector that ran and an embedder that was missing must leave the library with
face **boxes** — which the print validator's trim-zone check needs, and which
have nothing to do with identity — rather than with neither. Clustering is a
whole-library pass because a person's faces are spread across every event they
appear in; there is no partial answer to checkpoint, so the job is re-derived
whenever the face set changes and skipped when it has not.

### The alignment pairing is refused before any face is embedded

`models/configs/arcface-buffalo-l.json`: *"Feeding an unaligned crop does not
fail — it returns a confidently wrong embedding, which then clusters wrongly and
puts the wrong person in an album."* A crop warped from the **wrong five points**
fails identically and is worse, because the warp succeeds and looks right.
`yunet_5` and `insightface_5` are both five points and are not interchangeable.

So the analysis stage reads both model configs and **fails the run** when the
detector's `landmark_scheme` is not the one the embedder's template was built
for. Three layers, on purpose:

1. the configuration check above, which names two models rather than producing
   one error per face;
2. `MlRuntimeClient.infer_faces`, which refuses to send a crop whose scheme is
   not the required one;
3. the host itself (`preprocess._align_face`), which refuses the item.

A detection whose reported scheme disagrees with its own model config fails the
stage outright: the pin does not describe what ran, so no landmark from that run
can be trusted onto a template.

### Zero faces are eligible for automated output, and that is correct

The stage reports, every run:

```
N faces, 0 eligible for automated output, N awaiting review
```

`assign_identities` will not emit an `auto_high_confidence` assignment while its
calibrator reports `calibrated = False`. No `FittedCalibrator` exists — it
cannot be constructed without a measured precision, 2,000 labelled pairs and the
digest of the evaluation set — and no `Person` has been enrolled, because
nothing in this repository has a labelling surface yet. CLAUDE.md rule 5 is why:
an unmeasured automated path is exactly the way a wrong person reaches a printed
family album.

The album still gets face **safety** out of the run. Identity gates naming,
never safety: `records.face_boxes_for_layout` takes no assignment argument, so a
child whose parent has not consented to labelling is unnamed *and* protected
from the guillotine.

### The vacuous pass this closes

The album used to be planned with `faces=()`. Every placement reported
`face_count: 0`, so `face_in_trim_zone` passed without checking anything and the
gate CLAUDE.md rule 5 rests on could not fail. Now:

* every selected photo carries its stored rectangles into layout;
* a photo whose `face_count` disagrees with the rectangles the library holds
  **stops the book** (`--reanalyze-faces` is the remedy: `face_id` includes the
  detector's id and version, so an older library's faces are addressed
  differently rather than merely stale);
* the face rectangles are part of the album's `inputs_digest`, so a re-detected
  library re-plans instead of reusing a completed job.

`packages/album-engine/tests/test_validator.py::TestTheGateFiresOnARealLayout`
observes the gate failing on a real full-bleed layout, and observes the same
page passing vacuously when no faces are recorded. A gate that has never been
seen failing is not known to work.

**What still cannot happen in this pipeline:** an album stage placement in the
trim zone. Layout prefers an inset frame for a single photo, whose frame *is*
the safe box, and `layout_page` drops any arrangement whose placement is not
print-safe. So the validator's face gate is a backstop here rather than a
routine failure — but it now runs against real rectangles instead of an empty
set, which is the difference between a backstop and a formality.

### Three things a review of this wiring found, each reproduced first

None of them raised. All three were found by reading the code and then written
as a probe that failed before the fix and passes after it.

1. **A vector's space was read from a constant, not from the record.** The
   `faces` stage wrapped every stored vector as `arcface_buffalo_l_512`
   whatever the record said, which disables the one guard — `FaceEmbedding`'s
   space check — that stops two incomparable vectors producing a plausible
   cosine distance. Measured: a vector moved into `adaface_ir101_512` was
   clustered against arcface vectors with no error, no note and no count. The
   space now comes from the record, and a library holding more than one space
   fails the stage instead of clustering across them. The spaces are also part
   of the clustering job's identity, so re-embedding a library with a different
   recognition model re-clusters rather than reusing the previous model's
   answer (face ids address the *detector*, so they do not change on their
   own).

2. **`--reanalyze-faces` forgot what a human had answered.** A face_id is
   content-addressed over the detector and the box, so re-detection rewrites
   the *same* face — and rewriting it from the detection alone reset
   `minor_status` from `estimated_minor` to `unknown`, `excluded_from_sharing`
   from true to false, and `created_at` to the re-detection. Silent data loss
   (hard rule 7), failing in the unsafe direction. The stored minor-safety
   envelope and `created_at` are now carried across the rewrite.

   One conservative asymmetry, stated because it is a judgement call: a stored
   `confirmed_minor` is *not* handed to the assignment, because a confirmed
   minor's consent lives in the record while the assignment reads consent from
   a `PersonGallery` that is empty (nothing enrols yet). It is reported to the
   assignment as `unknown` — both are ineligible, so no face becomes nameable
   that was not already — and the stored envelope, consent included, is written
   back verbatim. When enrolment exists, this is the line that changes.

3. **Two detections in one 1e-4 grid cell were counted as two faces.**
   `face_id` quantises the box, so an overlapping pair is one id and one row,
   but `face_count` counted detections. That left `face_count: 2` against a
   single stored rectangle — which the album stage reads, correctly, as face
   evidence it cannot see — and refused the book permanently, with a
   remediation (`--reanalyze-faces`) that reproduces the state exactly.
   Detections are now de-duplicated by face_id before the summary is written.

4. **`face_id` was computed two ways, in one language.** The contract named the
   tuple and never said how it became bytes, so `ids.face_identity` picked its
   own — and picked twice. `round()` rounds half to even where JavaScript's
   `Math.round` and Rust's `f64::round` round half away from zero, and 8855 of
   the 10000 half-quantum positions in [0,1] are exactly representable, so a
   box landing on one got different ids from different writers. Worse, `!r` on
   the RationalTime components is Python's `repr`: a frame time that parsed as
   `1001.0` hashed differently from the same time parsed as `1001`, which is
   one frame with two ids *inside Python alone*, decided by whether the JSON
   had a decimal point. Issue #34 froze the encoding in
   `contracts/schemas/face-record.schema.json`, and this module now implements
   it and is pinned against `contracts/vectors/face-id.json` by
   `tests/test_units.py::FaceIdentity`.

   **Operational consequence, stated rather than left to be discovered:** a
   library analysed before this change holds face ids for the *old* rounding.
   They differ only where a box component landed exactly on a half-quantum, so
   the drift is partial rather than total — which is the worse kind. Re-run
   with `--reanalyze-faces`; the stored minor-safety envelope and human answers
   survive it (see 2 above). The `face:v1` domain tag was deliberately NOT
   bumped: v1 is what the contract now defines, and what shipped before it was
   not a different version, it was an undefined one.

### Known gaps, named rather than worked around

* **The demo library produces no album.** `scripts/demo/make_library.py` writes
  EXIF `DateTimeOriginal` with no `OffsetTimeOriginal`, so `captured_at.utc`
  stays null (correctly — a local time with no zone is not an instant) and the
  album stage refuses to build a chronology from undated media: *"1 cluster(s)
  found, none of them dated"*. The face path runs fully on that library; the
  book does not. Fixing it changes every file's bytes and therefore every
  `media_id`, so it is a deliberate separate change.
* **Real SCRFD finds none of the demo library's cartoon faces** (0 detections
  over 40 stills, at the configured 0.5 threshold). The library's own docstring
  says its faces prove nothing about detection quality; this is that warning
  coming true. Every face count reported by a run over it comes from the fake
  host in `tests/support.py`, which is a contract-shaped stand-in, not a
  detector.

## What is wired, and what is not

**Wired end to end and exercised by tests:** ingest → media-db → classical
quality → image embedding → face detection → face alignment + embedding →
face clustering → person assignment → review queue → fusion → dedupe → event
clustering → selection → **face-safe layout** → print validation → AlbumSpec →
PDF/X-4.

**Not wired:**

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

Five, all taken under issue #48, all **blocking defects the path in question
could not work around**. All are in Codex's territory or in the shared
`contracts/`, except the last, which is in Claude's own package but changes what
`workers/render-video` sees:

0. **`contracts/schemas/media-record.schema.json`** gained one property:
   `processing.stages.face_embedding`. The stages map is
   `additionalProperties: false`, so a second face step could not be recorded at
   all without it, and collapsing alignment+embedding into `face_detection`
   would have made "the detector ran" and "the faces were embedded" the same
   fact. Additive only; every existing record still validates. Bindings
   regenerated. **This needs Codex's sign-off before merge.**

0b. **`workers/ingest/src/media.rs` and `src/gopro.rs`** gained
   `face_embedding: None` in their two `ProcessingStateStages` initializers.
   Consequence of 0 above, and not optional: the generated Rust struct is built
   with an explicit field list in both places, so adding the property broke
   `cargo build` on the ingest crate — `error[E0063]: missing field
   face_embedding`. Ingest runs neither face step, so `None` is the truthful
   value. Caught by the local CI runner, not by review.

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

57 tests, about two minutes. They use the real Rust ingest binary on real JPEGs
with real EXIF, and a **fake model host that speaks real gRPC** over a loopback
socket using the generated stubs — a fake MODEL, not a fake transport, so every
message shape, error path and correlation rule is exercised for real. The fake
face embedder refuses an unaligned item, a landmark-less item and a
wrong-scheme item exactly as the real host does, which is what makes the
pipeline's own alignment guards testable rather than merely present. Set
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
