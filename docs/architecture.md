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

**PaddleOCR runs detection only, never recognition.** Text *coverage* answers
"is this a screenshot"; transcribing the words would capture bank balances and
medical results to answer a question that does not need them. A privacy
decision, not a cost one.

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
- **No album has been rendered.** The Phase 2 exit gate — a real library
  producing a 32-page PDF that passes the validator — has not been met.
- **No safety classifier is selected** (issue #21), so the release pipeline has
  no sensitive-content gate. That is a release blocker in its own right.
- **`DEFAULT_SHARPNESS_FLOOR` is uncalibrated** (issue #22) against a scale that
  does not exist yet, and it is a *hard* elimination gate — a wrong value
  silently discards real photos.
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
