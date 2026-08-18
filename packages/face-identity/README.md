# face-identity

Clustering, person assignment, and the review queue. This package owns CLAUDE.md
hard rule 5 — *"A wrong person in a family album is a catastrophic failure.
Automated output uses the high-confidence threshold; uncertain matches go to the
review queue."*

## The one-paragraph version

`cluster_faces` groups embeddings into person *hypotheses* under complete
linkage. `assign_identities` turns hypotheses plus a gallery into
`PersonAssignment`s. `PersonAssignment.eligible_for_automated_output` is a
**computed property, not a field** — there is no constructor argument for it and
no call site that can set it. Album, film and reel code takes an
`AutomatedFaceSet`, which cannot be constructed around an ineligible face.
Everything uncertain becomes a `ReviewItem`; answering one changes the evidence
and the next assignment pass re-derives the conclusions.

## Where this runs

Wired into `services/pipeline` (branch `feat/wire-face-identity`):

```
analysis stage   SCRFD detects           -> a FaceRecord per face, no embedding
                 ArcFace embeds          -> the host warps each face onto the
                                            insightface_5 template; the record
                                            gains a VectorRef
faces stage      cluster_faces           -> person hypotheses
                 assign_identities       -> assignments (all unassigned today)
                 build_review_queue      -> outputs/faces/review-queue.json
album stage      face_boxes_for_layout   -> the rectangles the print validator's
                                            trim-zone and gutter checks protect
```

Measured on the 46-file synthetic demo library (`scripts/demo/make_library.py`,
41 analysable stills after 5 quarantines) over the real gRPC contract with the
fake model host: **33 faces detected, 33 embedded, 33 clusters, 0 eligible for
automated output, 33 awaiting review**, every one of them queued as
`new_cluster`. Zero eligible is the correct number and is asserted as such —
see below.

The counts come from a fake host, and that is not a shortcut that could be
avoided: run the same library through **real** SCRFD weights and it finds *zero*
faces, because the library's "faces" are drawn cartoons and its own docstring
says they prove nothing about detection. What real weights do verify is the half
that does not need a real face — ArcFace loads, aligns through the
`insightface_5` template, and returns a 512-d vector `FaceEmbedding` accepts, and
the same face warped from a differently-ordered five points comes back as a
different vector (cosine 0.9519 against itself). That difference is the failure
mode the scheme checks exist for, and it is invisible to everything downstream.

The alignment pairing is refused at configuration time: the analysis stage reads
both model configs and fails the run when the detector's `landmark_scheme` is
not the one the embedder's template was built for, rather than embedding a
library's worth of faces off a plausible warp.

## What is NOT claimed

**No face-recognition precision number appears anywhere in this package,
because none was measured.** There are no ArcFace weights in this repository and
no real faces to run them on. The `≥99% precision` gate in build plan §7 is not
met, not missed, and not estimated here — it is *unmeasured*.

That is enforced rather than noted:

* `assign_identities` will not produce a single `auto_high_confidence`
  assignment unless its `Calibrator` reports `calibrated = True`.
* The only calibrated implementation, `FittedCalibrator`, cannot be constructed
  without a measured precision at or above the target, at least 2,000 labelled
  pairs, and the BLAKE3 digest of the evaluation set.
* The default, `UncalibratedSimilarity`, reports `calibrated = False`. With it,
  every match that would have been automatic goes to the review queue instead.

So the automated path is **closed until somebody measures it**. A fresh install
sends everything to a human.

## The eval that would measure precision

Not implemented — it needs data this repository does not have. Specified so that
the first person with real embeddings can run it without re-deciding anything.

**Inputs.** A labelled benchmark library: ≥50 identities, ≥30 faces each,
spanning the six benchmark categories (build plan §6), with per-face ground
truth `person_id` and per-face metadata for the slices below. Identity labels
come from a human, not from clustering. The set is content-addressed; its
BLAKE3 digest is `FittedCalibrator.inputs_digest`.

**Procedure.**

1. Embed every face with the pinned ArcFace model (`ModelRef` incl.
   `weights_blake3` and `config_blake3` — the alignment template lives in the
   config and changes every number here).
2. Split into an enrolment half and a probe half, per identity, by a fixed seed.
   Enrolment simulates the gallery a user builds by confirming faces.
3. For every probe face, score it against every enrolled identity with
   `PersonGallery.similarity` (this is where `top_k` gets measured rather than
   guessed — sweep k ∈ {1, 3, 5, all}).
4. Sweep the operating similarity `s` over [0, 1]. At each `s`, a probe is
   *claimed* when its best similarity ≥ `s` and it beats the runner-up by
   `ambiguity_margin`. Precision = correct claims / claims. Recall = claims /
   probes.
5. **The operating point is the smallest `s` whose LOWER Wilson 95% confidence
   bound on precision is ≥ 0.99.** The lower bound, not the point estimate: a
   point estimate of 0.994 over 300 claims is compatible with 0.98, and the
   product promise is about the true rate.
6. Report the same sweep per slice, and gate each slice separately:
   category; face area ratio (the schema calls it "the single best predictor of
   whether an embedding will be trustworthy"); |yaw| above and below 45°;
   estimated age band; skin tone band. **A model that hits 99% overall while
   sitting at 94% on children's faces has failed**, and only the per-slice gate
   can see that — this is the same argument `harness.py` makes for why the mean
   cannot pass a run.
7. Emit a `FittedCalibrator` with the chosen `s`, the measured precision, the
   claim count, and the inputs digest.

**Gating.** The result becomes a real gate file in
`packages/eval-harness/gates/`, one case per (category × slice), metric
`identity_precision`, `expected` set to 0.99, `enforce_expected: true`. A model
swap that drops any slice below its floor fails CI with exit 1.

**Also worth measuring, and not by this eval:** the false-merge rate of
clustering on real embeddings (needs the same labelled set, measured as
pairwise precision per cluster), and the review queue's *yield* — how many faces
become eligible per human tap. The second is the number that decides whether the
product is usable, and it can only be measured with a real user.

## What IS measured

`memory_engine_face/eval.py` runs a **synthetic clustering benchmark**: six
generated libraries, one per benchmark category, whose ground truth it produces
and therefore knows exactly. Pairwise precision *and* recall are gated per
category (precision alone is trivially gamed by grouping nothing).

The committed measurement lives in
`packages/eval-harness/gates/face-clustering-synthetic.gate.json` and runs in CI
with the other gates. `tests/test_eval.py` re-runs the benchmark and asserts the
numbers match that file exactly, which is what catches a change that moves the
measurement — a committed gate file cannot notice its own staleness.

At `merge_threshold = 0.50` the algorithm sits at 1.000 on eleven of the twelve
cases, with drone recall at 0.829. That makes the suite a **regression
detector**, not a difficulty measure: there is no headroom to improve into, and
any loosening (single linkage merging the lookalike pairs) or tightening (more
over-splitting) breaks a floor. These are Gaussian blobs on a sphere; real
ArcFace embeddings are not, and these numbers say nothing about real faces.

## Minors: what was assumed

The schema has a minor-safety envelope and says `unknown` "is NOT treated as
adult" and "'nobody asked' is not the same as 'no'". Two separate gates were
derived from that, and they are separate because conflating them breaks one:

| | may carry a `person_id` | eligible for automated output |
|---|---|---|
| `confirmed_adult` | yes | yes |
| `confirmed_minor` + live consent | yes | yes |
| `confirmed_minor`, no consent | **no** | no |
| `estimated_minor` | yes | **no** |
| `unknown` | yes | **no** |

**Assumption, and its cost.** A freshly scanned library produces **zero**
eligible faces until somebody resolves minor status, because `unknown` is the
schema default and there is no age model in the registry. The first automated
album cannot name anybody until the user answers a handful of "is this a child?"
questions.

That is survivable only because **identity eligibility gates naming, never
safety**. The print validator's trim-zone and gutter checks run on face *boxes*
and need no identity at all: `records.face_boxes_for_layout` returns every
detected face over the detector floor and does not accept an assignment
argument. Wiring the two together would turn a privacy control into a print
defect — a child whose parent has not consented to labeling would have their
face excluded from the safety check and cut in half by the guillotine.

Other conservative choices, stated because they are judgement calls:

* A human tapping a name on a `confirmed_minor` face with no consent ledger
  entry is **refused**, not honoured-with-a-warning. The tap is not the consent.
* Learning that somebody is a child **strips the name retroactively** from every
  face of theirs, including faces already assigned and already used.
* Answers that CLOSE a gate propagate to the whole cluster; answers that OPEN
  one apply only to the faces the human was shown. `confirmed_minor` covers the
  group; `confirmed_adult` covers the face on screen. The durable "this person
  is an adult" answer is `Person.minor_status` in the gallery, which the next
  assignment pass applies through the identity path (which has thresholds)
  rather than the clustering path (which does not).

## Known contract gap

Four review reasons this package needs have no equivalent in
`FaceRecord.identity.review_reason`: `minor_consent_required`,
`minor_status_unresolved`, `uncalibrated_threshold`, `no_embedding`. They
serialise as `null` rather than as the nearest available value, because
reporting `near_boundary` for a face held back by a missing consent is a
plausible, wrong explanation. The real reason stays on the review item.

Widening the enum is a `contracts/` change and needs Codex's sign-off, which is
blocked on issue #48. Raise it when Codex is back rather than editing the schema
unilaterally.

## Known limits

* Clustering is O(n²) memory and O(n³) worst-case time in the number of
  representatives. Fine for the thousands a family library produces; **not fine
  for 100k**. A blocking pass (pHash / coarse-centroid canopies) is needed
  before it and is not written.
* `method` is reported as `agglomerative_cosine`, not `hdbscan_cosine`. HDBSCAN
  is what the schema and build plan name; it is not installed and is not
  vendored here, and claiming it while running something else would be the
  silent model swap hard rule 7 forbids. `membership_strength` is consequently a
  scaled proximity, **not** an HDBSCAN membership probability.
* `FittedCalibrator` interpolates linearly away from its operating point. Only
  the operating point is calibrated; the `review_floor` comparison inherits the
  crudeness of the interpolation. A real calibrator (isotonic or Platt over the
  sweep in §"The eval that would measure precision") should replace it.
* `PersonGallery.top_k = 3` is a guess, not a measurement. Step 3 of that eval
  measures it.
