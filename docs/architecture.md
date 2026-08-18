# Architecture

What exists, what it guarantees, and what is still a plan. Written to be read by
whoever picks this up next — including either agent after a context reset.

`docs/memory-engine-build-plan.md` is the intent. This is the record of what was
actually built and what building it taught us.

---

## The one thing to know first

**Every real defect found in this system so far has been silent.** Not one
raised an exception. They returned plausible numbers and were wrong:

| Defect | What it looked like |
|---|---|
| A preprocessing scale applied twice | Every pixel collapsed into a 0.016-wide sliver near −1 |
| One detector's box decoder used for another | A detector that seemed mediocre |
| YuNet confidence as a product, not a geometric mean | ~84% of real faces discarded at the configured threshold |
| A load gate whose mismatch test needed two non-null values | Unverified weights loading cleanly in **release** mode |
| `NaN` sharpness | Passed an elimination floor untouched, because `NaN < x` is `False` |
| A config digest re-serialised rather than byte-hashed | Python `1.0` vs JavaScript `1` — every model reporting a mismatch |
| A `span_id` fixture written by hand | A content-addressed identity test pinning an invented number |
| A 5-band raster re-wrapped as 4 channels | Every album page sheared; one photo smeared across the full width and repeated ~5× |
| CMYK ink read back as RGBA | The K band became a 5% alpha; every printed photo flattened to near-white |

This is why the codebase looks paranoid. Nearly every guard here exists because
the thing it guards against already happened.

**A green test suite is weak evidence.** Four modules once passed all their
tests while an independent check found 43 of 85 mutations surviving in one of
them. Check *what the test exercises*: a fixture that starts mid-pipeline tests
the step *after* the bug.

---

## Ownership

Two agents. `AGENTS.md` and `CLAUDE.md` hold the binding version.

| Claude — intelligence | Codex — shipping |
|---|---|
| `contracts/` (drafting; both sign off) | `workers/ingest`, `workers/ml-runtime` |
| `packages/media-db` | `workers/render-*` |
| `packages/ranking-engine` | `apps/*`, `services/*` |
| `packages/album-engine` | `.github/` |
| `packages/story-engine` | |
| `packages/prompt-engine` | |
| `packages/eval-harness` | |
| `models/`, `docs/` | |

The two review each other and merge without the owner, who is not an engineer.
That loop has found substantially more than either agent found alone: of 37
defects, **none** were found by the author of the code.

---

## The contract layer

Ten documents. Eight JSON Schemas, two protos.

```
contracts/schemas/       MediaRecord  FaceRecord  MomentRecord  EDL
                         AlbumSpec  JobSpec  PrefEvent  SafetyClearance
contracts/proto/         ml_runtime.proto   media_query.proto
contracts/codegen/       -> pydantic, TypeScript, Rust
contracts/fixtures/      44 golden fixtures, each with a stated purpose
```

Fixtures are three-way: `valid`, `schema-invalid` (must be **rejected**, naming
the field the rejection must point at), and `semantic-invalid` (passes the
schema, violates a cross-field rule). A fixture cannot be added without saying
what it is for.

### Conventions that are load-bearing

**Time is `RationalTime {value, rate}`, never float seconds.** 30000/1001 has no
exact float form, and a long timeline accumulates drift. Maps 1:1 onto OTIO.

**Coordinates are normalised against the *oriented* image** (EXIF applied),
origin top-left. This is what lets a face found on a 512px proxy be cropped from
a 6000px original with no rescale step anyone can forget. Exactly one exception,
named in the type: landmarks accompanying an inline tensor are normalised to
that tensor's own extent, because a bare 112×112 crop has no image to refer to.

**Identity is content-addressed** (BLAKE3), which is what makes every job
idempotent. Digests are over **bytes**, never a re-serialisation — Python writes
`1.0` where JavaScript writes `1`.

**A hard cut is the absence of a Transition.** Derived fields are not exported.

---

## Pipeline

```
   sources
      │  ingest: BLAKE3 → EXIF → pHash → 512px thumb → 480p proxy + frame index
      ▼
  MediaRecord ──────────────► media-db  (SQLite, FTS5, sqlite-vec)
      │                            │
      │  analysis (proxies only)   └──► MediaQuery service ──► desktop shell
      ▼
   ml-runtime ── SigLIP 2 · face detect/embed · IQA · OCR · safety
      │
      ▼
  ranking-engine ── fusion → dedupe → primary selection
      │
      ├──► album-engine ── cluster → select → layout → VALIDATE ──► render-print
      └──► story-engine ── moments → beats → reel plan ──► EDL ──► render-video
                                 │
                          prompt-engine ── contact sheets ──► frontier model
```

**Source files are opened exactly twice in their life**: at proxy generation and
at final render. Everything between touches proxies only, and the ml-runtime
proto has no field anywhere that can hold a path — enforced by a test, because an
absence is easy to reintroduce by accident.

---

## The gates, and why each fails closed

Three gates stand between a bug and something irreversible. All three were built
or repaired after a fail-open was found in one of them.

### Model load gate — `models/policy/load_gate.py`

Two modes plus one absolute rule. `release` requires registration, pinned weights
*and* config, a verified licence, and no commercial blocker. `development`
relaxes verification behind an explicit environment opt-in and warns on every
load. **A hash mismatch is fatal in both.**

Fail-opens found and fixed here:
- a pin that was never *checked* passed, because the mismatch test needed both
  values while the pinning test looked only at the pinned one
- `placeholder` status was recorded in three places and enforced in none

`resolve_mode` defaults to `release`, so forgetting to configure anything fails
closed.

### Print validator — `packages/album-engine/validator.py`

Five hard gates: `dpi_floor`, `face_in_trim_zone`, `bleed_coverage`,
`color_profile_match`, `page_count_valid`. The AlbumSpec schema *requires* a
passing report to contain every one, so a pass cannot be asserted by omission.

**A check that cannot run fails.** If DPI is uncomputable because source
resolution is unknown, that is a failure, not a skip. A book is printed once and
cannot be patched in the post.

### Safety clearance — `contracts/schemas/safety-clearance.schema.json`

Bound to an exact publication — this sink, these ids, in this order, under this
classifier and config digest — and hashed. The renderer verifies against the
inputs it is *actually* publishing, inside the same operation, so there is no
check-then-swap window.

**Absence is `indeterminate`, and indeterminate blocks.** Missing result,
unloadable model, digest mismatch, stale evidence, timeout — all block.

A *positive* result may be overridden per item by a named human; a parent may
decide a breastfeeding photo belongs in the family book. A *missing* result may
not be overridden by anything. "Nobody checked" and "somebody checked and
disagreed" are different states.

---

## Decisions taken, with their reasons

**Face stack is SCRFD + ArcFace**, chosen on accuracy for internal use. Their
weights are non-commercial, contained structurally rather than by memory:
`min_load_mode: development` plus `blocks_commercial_release`, with a
licence-clean YuNet path kept working so the swap is mechanical.

**Detector postprocessing happens in the host**, returned as typed
`DetectionSet`. Otherwise it gets written once in Python and once in Rust and
they drift — and a box wrong by an anchor stride still looks like a box.

**Boxes are clipped to [0,1]; landmarks are not.** A face clipped by the frame
genuinely has an eye off-frame, and clamping it moves the alignment template,
producing an embedding that is confidently wrong rather than absent. A detection
with no overlap at all is *dropped*, not clamped — the letterbox padding band is
uniform grey and detectors do fire on it.

**Missing quality signals renormalise; they do not default.** Zero punishes
photos the expensive models have not reached; 0.5 fabricates a measurement.
Coverage is reported separately, and scores measured differently refuse to be
ranked against each other.

**An uncalibrated measurement may rank; it may not eliminate** (issue #22).
`DEFAULT_SHARPNESS_FLOOR = 0.08` was a hard gate against a scale that does not
exist — ingest writes no sharpness and no Laplacian-to-Unit normalisation is
implemented anywhere. Measured on the synthetic library, that same constant
eliminates between 0.0% and 77.5% of the same 200 images depending only on which
divisor the missing normalisation would have used. Calibrating it against
synthetic blur was rejected: the library has no blurred variants at all, and the
hard negatives that matter — a dim handheld shot of a first birthday, a long
exposure, a shallow-depth-of-field portrait — cannot be drawn with PIL. So the
default floor is `None`, sharpness keeps the heaviest fusion weight, and
elimination is gated behind a `SharpnessFloor` that must name its normalisation
and retain every hard negative. See `docs/sharpness-floor-decision.md`.

**A capability claim about a graph must cite what established it** (issue #31).
YuNet declared `max_batch: 8, dynamic_axes: true` against a checkpoint whose
input is fixed at `[1,3,640,640]`. The runtime clamping to the real leading
dimension is what let the wrong config keep passing review, so the config is now
where it fails: `batching.verified_against` is required, and any claim beyond
one-at-a-time without one is schema-invalid.

**The padded band is config, not a runtime constant** (issue #33). Geometry was
pinned and contents were not, so PR #25 filled it with black because nothing said
otherwise. `preprocessing.pad_value` pins the value *and the space it applies
in* — mmdetection pads after normalising, so its `pad_val=0` means the mean, not
black — and is required for any letterbox config, with `null` reserved for "the
sources disagree".

**PaddleOCR runs detection only, never recognition.** Text *coverage* answers
"is this a screenshot"; transcribing the words would capture bank balances and
medical results to answer a question that does not need them. A privacy
decision, not a cost one.

**"We cannot decode this yet" is not a quarantine** (issue #14, finding 1). The
review argued that quarantining HEIC under `unsupported_codec` was wrong, and it
was right. `file_corrupt` and `zero_byte_file` are permanent properties of the
file; `unsupported_codec` was a property of *our build*, and quarantine means
never retried automatically — so every iPhone photo scanned before the decoder
landed would have stayed dead until something forced a re-scan. A missing
capability is now `failed` with `retryable: true`, quarantine is reserved for
files that are genuinely hostile, and a completed scan re-tries records parked in
the old terminal state when the capability appears. A decoder landing is a
re-run, not a migration.

**The perceptual hash is named, not just measured** (issue #14, finding 2).
`phash-dct-64` hashed the 8×8 low-frequency block *including* `C(0,0)`, the sum
of every sample, while taking the threshold from the other 63. DC is above that
threshold for every input that is not exactly black — measured, 27 of 28 images,
against 9–20 of 28 for every other position — so the top bit was a constant, and
the first of the four 16-bit bands the dedupe index uses carried fifteen live
bits. `phash-dct-64-v2` drops DC and appends `(0, 8)`, keeping 64 informative
bits rather than shipping 63 and a pad.

The bit was worth one band's collision rate and nothing more. What made the
change worth a migration is that **`phash-dct-64` never defined its own bits**:
the reference implementations disagree about the threshold statistic and about
whether DC participates, so two writers could both be "phash-dct-64" and produce
unrelated digests. The encoding is now frozen on the schema, with golden
luma-matrix → digest vectors recomputed independently in Python and Rust. The
migration is paid once, and it will never be cheaper than before a real library
exists.

Two limits are stated rather than implied. The step from a file to the 32×32
luma matrix is *not* portable — `image` greys with Rec. 709 weights and integer
division, Pillow with Rec. 601 — so digests are comparable only between records
written by the same producer, which is why the vectors start at the matrix.  And
**equal length is not equal meaning**: `phash-dct-64`, `phash-dct-64-v2`,
`dhash-64`, `ahash-64` and `wavelet-64` are all sixteen hex characters, and
`hamming_distance` guarded on length alone, so every cross-algorithm pair passed
and returned a number with no referent — which dedupe acts on by dropping a photo
from every automated output. The algorithm is now part of the band key, part of
the comparison, and an indexed column in media-db, backfilled from the records so
nothing needs re-scanning.

---

## Status

Green on `main`: contracts + codegen + fixtures, media-db, ranking-engine
(dedupe + fusion), model registry + load policy, both protos, ingest (stills,
macOS/Windows video proxies, HEIC), desktop shell v1.

**Real inference works.** YuNet detected a face on a real photograph with CPU and
CoreML agreeing at ~0.9135; SCRFD at 0.790975, exercising the two-anchor decoder.
That is the first time anything here has touched real imagery rather than
fixtures.

Open, and honest about it:

- **story-engine and prompt-engine are partial.** `moments.py` and `reel.py` are
  the two largest files in the repo and have **no tests**. Given the 43-of-85
  measurement, untested code from an agent that did not finish is not evidence.
- **An album has now been rendered, and rendering it found the worst defect in
  the repo.** A 22-page PDF/X-4 comes out of the synthetic library and passes the
  validator. Getting there needed two fixes. `scripts/demo/make_library.py` wrote
  no `OffsetTimeOriginal`, so `captured_at.utc` was null for every file and the
  album stage correctly refused undated media — no album could ever be planned.
  Then the PDF was wrong: `workers/render-print/src/page.ts` round-tripped the
  page through `.raw()` and re-wrapped a **five-band** CMYK+alpha buffer as
  **four** channels, and converted the result a second time as if it were RGBA.
  Measured on a 306mm page with one declared 121.8×91.3mm placement at
  (92.1, 107.3): content covered 27.1% of the page with a bounding box spanning
  the whole of it, instead of the declared 11.9% in the declared frame, and the K
  band read as a 12/255 alpha flattened every photo to near-white. **The print
  validator passed every one of those PDFs**, because it validates the AlbumSpec
  and the renderer did not execute it — the gate CLAUDE.md rule "a PDF below the
  DPI floor cannot be exported" rests on was measuring a plan nothing followed.
  Nothing caught it because the only assertion on the artifact was `%PDF` and
  "bigger than 100kB". `test/page-geometry.test.ts` now measures the raster
  differentially and fails on both halves of the old behaviour.
  **The Phase 2 exit gate is still not met**: the plan asks for a real library,
  and this is a synthetic one whose photo selection came from a stand-in embedder
  (see below), so what is proven is the chain from AlbumSpec to paper, not the
  taste of what went on it.
- **The image embedder has no weights and cannot be fetched.**
  `siglip2-so400m-384` is absent from `models/weights/` and its
  `weights.source_url` is a Hugging Face model *page*, not a file, which
  `scripts/models/fetch_weights.py` explicitly cannot resolve. Analysis requires
  it alongside the two face models, so **on a machine with the three real
  checkpoints (YuNet, SCRFD, ArcFace) the album path still cannot run** — it
  reports `siglip2-so400m-384 (weights_missing)` and album and render-print
  refuse. Every album rendered so far used the test suite's stand-in host. The
  reel is unaffected; the video path does not go through SigLIP.
- **The video path runs, and is missing four producers.** `services/pipeline`'s
  `story` stage now drives 480p proxy → FeatureStream → `plan_moments` →
  `plan_reel` → `render-video` and produces a playable file from a synthetic
  library. What it does NOT have: a transcript backend (so **no cut is certified
  word-safe** — the EDL carries no `no_mid_word_cut` finding at all), face and
  smile detection, audio-event classification, and any bundled music (so **no cut
  is beat-locked**; the <50ms downbeat gate in the build plan's success criteria
  cannot be measured yet). All four are counted in the stage result and printed.
- **`MediaRecord.video` is never populated.** `VideoProperties` exists in the
  contract with `oriented_size`, `rotation_deg` and `is_variable_frame_rate`, and
  `workers/ingest` writes null. The source's pixel geometry is therefore
  unmeasured, which is why the reel disables reframing and targets the 480p
  proxy raster rather than a 1080p master. This is the single change that would
  make the reel a deliverable product rather than a proof the chain runs.
- **No film planner exists.** `story-engine` plans reels only; the film planner
  is a phase 5 item. The runner says so rather than relabelling a reel.
- **No safety classifier is selected** (issue #21), so the release pipeline has
  no sensitive-content gate. That is a release blocker in its own right.
- **SCRFD's letterbox padding value is unresolved** (issue #33). Two upstream
  references disagree by a full unit in tensor space and training used a stretch,
  so there is no trained-with value to recover. Pinned as `null`, refused by the
  release gate, and settled only by running both values against the published
  benchmark — see `docs/preprocessing-padding-decision.md`.
- **No checkpoint in the registry is pinned.** Every `weights.blake3` is null, so
  every capability claim about a graph is inherited rather than measured. Since
  issue #31 that is at least *visible*: `batching.verified_against` is required,
  and eight of nine entries now say batch 1 because nothing has read their input
  shape.
- **A structureless frame's perceptual hash is rounding residue.** Found while
  freezing the pHash encoding. Every hashed coefficient of a flat field is
  mathematically zero, so the threshold is drawn from the rounding cloud and all
  64 bits are decided by summation order. Measured: a flat field's largest
  coefficient is ~9e-11 where a real frame's smallest is 0.77, and flat fields at
  luma 1, 17, 64, 128, 200 and 255 give four distinct digests 27–36 bits apart
  while 1, 64 and 128 collide exactly. `phash-dct-64` had this too; dropping DC
  neither caused it nor cured it. Pinned by a test that asserts the *diagnosis*
  rather than the digests — the digests are not portable and must never be
  frozen — and left for its own decision, most likely writing no `image_hash` at
  all, which dedupe already handles, rather than inventing a structure
  threshold. It is also why no flat or separable pattern is a golden vector.
- **Nothing has been tested on a large real library.** Every performance claim
  in this repo is untested at scale.

---

## Working notes

- Mutation-test before believing a suite. **Clear `__pycache__` first** — a
  `min`→`max` mutation keeps file length identical, and same-second writes share
  an mtime, so CPython serves a stale `.pyc` and the mutation looks killed.
- Match mutations by line number, not by string: a pattern that also appears in
  a docstring will hit the prose and report a false survivor. That happened.
- Assert *behaviour*, not platform float results. A haversine clamp test
  asserting a two-ulp overflow passed on macOS and failed on Linux CI.
- Pin calibration constants by what they produce, from both sides. The bleed
  epsilon is bracketed by a 12-inch trim where noise lands 5.7e-14mm short and a
  0.001mm under-bleed that must still fail — without asserting `1e-6`.
- A skip must never share an exit code with a pass. Found three times, in three
  different files, by both agents.
