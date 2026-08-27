# Photeo expert plan — reconciled with measured evidence

**Status:** live implementation guidance, reconciled 2026-08-27.

This document supersedes the outside expert's original M0–M9 proposal. The original
is preserved in Git history; it is not retained inline because several of its central
claims are now falsified, and leaving them beside corrections would make them too easy
to mistake for current guidance. For a two-minute version, read
[`EXPERT-PLAN-STATUS.md`](EXPERT-PLAN-STATUS.md).

This is a plan for the current Android/React Native product in `apps/mobile`. It does
not supersede the product architecture and ownership rules in
[`memory-engine-build-plan.md`](memory-engine-build-plan.md).

## 1. How to read this plan

The status words are deliberate:

- **SHIPPED** means the production path uses it.
- **BUILT / FLAG OFF** means an implementation and reproducible comparison exist, but
  the production path does not use it.
- **MEASURED / REJECTED** means the proposed mechanism lost on the measured library or
  fixtures. Do not repeat it without new evidence that changes the premise.
- **REJECTED ON MECHANISM** means the method is invalid for the proposed inputs even
  though no product A/B was run.
- **BLOCKED ON MECHANISM** means the named implementation route cannot exercise the
  claimed behavior. It is not evidence that a different runtime or graph could not.
- **OPEN** means the milestone still has product work to do.

Numbers below come from tracked measurements or reproducible repository harnesses.
Device-specific results are not generalized to other hardware unless the graph
structure itself makes the result portable.

## 2. What from the expert plan still stands

The following are still live guidance:

1. Separate per-photo quality from album-level value. A photograph can be good but
   redundant, or imperfect but uniquely important.
2. Keep analysis versioned, incremental, resumable, deterministic, and local. Missing
   evidence stays unknown rather than becoming false certainty.
3. Persist expensive signals once per asset/model/preprocessing version. Album builds
   should consume stored evidence rather than rerun inference.
4. Keep hard constraints for actual product invariants, especially user exclusions and
   near-duplicate suppression. Use soft evidence for taste.
5. Evaluate model and scoring changes on decisions and held-out events, not only mean
   embedding similarity.
6. Keep privacy, sensitive biometric handling, bounded memory, crash-safe writes, and
   deterministic fallbacks as standing requirements.

The plan's detailed prescriptions for TinyCLIP quantization, multi-prototype identity,
facility location, DPP evaluation, the M1 parse fix, and the framing tie-break are no
longer live guidance. Their measured disposition is below.

## 3. Corrected measured baseline

### 3.1 Deep analysis is slow, but the timer is not kernel time

On the owner's OPlus CPH2649, a real 3,000-photo build reported:

```text
deep-analysis                    148,837 ms / 64 photos   2.33 s/photo
tinyclip.model-inference span    145,952 ms total         2,280 ms mean
movenet.model-inference span      71,113 ms total         1,111 ms mean
```

TinyCLIP is the longer concurrent span, but `model-inference` is `Date.now()` around
`await model.run(...)`. With `ANALYZE_CONCURRENCY = 6`, resolution returns to the JS
thread while other photos can be decoding JPEGs and normalizing tensors there. The
span is therefore **kernel execution + JS scheduling delay + contention**, not pure
inference. The two model spans overlap inside one `Promise.all`; they must not be
summed. Evidence: [`DEEP-ANALYSIS-TIMING.md`](DEEP-ANALYSIS-TIMING.md) and
`apps/mobile/src/ml/tinyclip.ts:97-128`, `apps/mobile/src/build-album.ts:70-83`.

The environment attribution is supported by a cross-check, not by intuition. On the
measured Mac at one thread, TinyCLIP fp32 took 6.03 ms, MoveNet int8 2.14 ms, and
w600k fp32 3.93 ms. Comparing a deliberately generous 20x phone projection with the
device spans leaves an approximately 19–26x multiplier on both an fp32 ViT and an int8
CNN. That shared multiplier points to the execution environment. It does not prove
which part is scheduling, contention, runtime overhead, or device throttling.

MoveNet currently overlaps TinyCLIP, so it adds no wall time while TinyCLIP remains
longer. It does set the current-path floor: even if TinyCLIP took zero time, the stage
would remain near the observed 1,111 ms MoveNet span under the same load. MoveNet is
also still used for pose diversity, so deletion is not an M3 optimization.

### 3.2 TinyCLIP quantization was tried and rejected

A quantized model **can** be produced from the shipped `.tflite` without the source
checkpoint: ai-edge-litert 2.2.0 exposes `CalibrationWrapper` for flatbuffer-to-
flatbuffer post-training quantization. Feasibility is settled.

The result is not viable:

| Variant | Result |
|---|---|
| Full int8 TinyCLIP | Conversion stops at `DIV`: `Quantization not yet supported for op: 'DIV'`. |
| Mixed int8 TinyCLIP | 33.2 MB → 8.5 MB, but 6.55 ms → 22.15 ms on the measured Mac: 3.4x slower. |
| Mixed graph structure | 71 `QUANTIZE`/`DEQUANTIZE` boundary ops surround the unconverted regions. |

This is a bad conversion, not evidence that int8 arithmetic is intrinsically slow.
The shipped graph has zero `FULLY_CONNECTED` ops; 61 of 81 `BATCH_MATMUL` ops have a
constant weight operand. Its 22 LayerNorms are decomposed into raw arithmetic: 44
`MEAN`, 23 `SQRT`, 23 `DIV`, and 22 `SUB` ops. That shape defeats the quantizer and
gives XNNPACK the wrong operators. The experiment and structural counts are recorded
in [`DEEP-ANALYSIS-TIMING.md`](DEEP-ANALYSIS-TIMING.md); fidelity gates remain in
`apps/mobile/src/quant/`.

The 3.4x slowdown was measured on a Mac and may not transfer numerically to ARM. The
71 conversion boundaries, missing `FULLY_CONNECTED` ops, and full-int8 `DIV` blocker
are properties of the graph and do transfer. Re-conversion from the upstream
checkpoint or a different runtime remains a legitimate new experiment; re-running
mixed int8 on this flatbuffer as though it were untried does not.

### 3.3 FP16 CPU is blocked on mechanism in the current integration

The original ladder treated “FP16 weights on XNNPACK CPU” as an acceleration rung.
Default XNNPACK expands fp16 weights to fp32 at load unless its `FORCE_FP16` flag is
set. The pinned `react-native-fast-tflite` 3.0.1 API constructs default delegate
options and exposes no route to that flag; the app calls the runtime with an empty
delegate list (`apps/mobile/package.json:28`, `apps/mobile/src/ml/README.md:27-37`,
`apps/mobile/src/ml/tinyclip.ts:140-152`).

No FP16-CPU TinyCLIP run was performed. The conclusion is narrower: **the named
mechanism cannot be exercised through the current wrapper**, so the plan's 1.4 s
target cannot be credited to that rung. A custom LiteRT wrapper that exposes the flag,
or another runtime, would be a different experiment.

### 3.4 M1's parse premise was already fixed; memory is the live problem

The original 6,694 ms atomic observations parse was real, but it is not the launch
path anymore. Embeddings were split out of the launch index. A later launch measured
`readMs=35 parseMs=545`, and the on-demand observations parse now yields every 500
rows. The device number is preserved in commit `8d316e8`; the current split and chunk
size are in `apps/mobile/src/faces/face-index.ts:45-70` and
`apps/mobile/src/faces/face-index.ts:1366-1384`.

The remaining measured cost is lifetime memory. On 17,768 faces × 512 dimensions,
the app's `number[]` representation consumed 89.5 MB versus 15.5 MB for the incoming
`Int8Array`, a 5.8x ratio and 74.0 MB difference. Once observations load, there is no
release path. This is resident-process memory and a background-kill risk; it is not
the ART Java heap that produced the separately observed album-build OOM. Evidence:
[`EMBEDDING-MEMORY.md`](EMBEDDING-MEMORY.md).

The harness ran under V8 rather than Hermes. The representation ratio is the finding;
the exact runtime-specific absolute constant is indicative.

### 3.5 M4 multi-prototype identity lost to the centroid

The proposed 1–6 prototype representation was built in shadow form and measured on
the owner's library. It does not beat the shipped centroid:

- Of 937 people with at least two faces, zero had mean intra-tile cosine below the
  library's calibrated assignment bar of 0.448; p05 was 0.477 and the median 0.625.
  The premise that one tile spans several appearances is false here. Infant drift is
  between tiles, not within a tile for a prototype to separate.
- At a 60-impostor budget, the centroid gained 152 presumed merges, the appearance
  split 161, and a random partition with the same `k` and piece sizes 162. The small
  gain is the maximum over `k × k` pairs, not appearance modelling.
- At impostor budgets 0, 2, 4, and 8, every measured policy gained zero merges.

`MULTI_PROTOTYPE_ENABLED` remains false and the module is not wired into clustering.
Evidence and reproduction: `apps/mobile/src/faces/face-prototypes.ts:1-69` and
`scratch/multi-prototype/measure.ts:1-110`.

This rejects multi-prototype activation **on this library and model**, not on all face
libraries. A new embedding model, materially different library, or corrected capture-
time evidence may justify rerunning the same harness.

### 3.6 M6 facility location was built, measured, and left off

The submodular path and its ablations were run on the three pinned 64-candidate,
24-photo fixtures. Removing facility location from the full objective changed only
1, 0, and 1 selected photos:

| Fixture | Full objective vs shipped | Remove facility: photos changed vs full | Facility only: moment / people coverage |
|---|---:|---:|---:|
| birthday | 3 / 24 | 1 / 24 | 6/8 moments, 5/6 people (full: 8/8, 6/6) |
| twoyears | 1 / 24 | 0 / 24 | 14/16 moments, 5/6 people (full: 16/16, 6/6) |
| trip | 2 / 24 | 1 / 24 | 7/7 moments, 4/4 people (no coverage gain over full) |

The birthday near-duplicate count fell from one to zero under the full objective, but
it also stayed zero with facility removed and even with both facility and coverage
removed. The win came entirely from the hard near-duplicate constraint now present in
the planner. Reproduce with:

```sh
node --experimental-strip-types apps/mobile/src/selection/album-selector-ab.ts
```

The production default remains `selector: "coverage-keys"`; see
`apps/mobile/src/selection/album-planner.ts:8-17` and `:100-148`. The M6 code remains
an offline experiment, not a pending rollout.

The DPP alternative is **rejected on mechanism for the proposed similarity blend**.
A DPP requires a positive-semidefinite kernel. The product's blend of semantic cosine,
face-set overlap, pose, place, and time has no PSD guarantee; with a non-PSD kernel,
`log det(L_S)` is undefined for some subsets and MAP optimization can silently optimize
garbage. No DPP product A/B was run. A future DPP would first need an explicit feature
map `L = diag(q) ΦΦᵀ diag(q)` or another construction that proves PSD; it cannot reuse
the current blended matrix. Evidence: `apps/mobile/src/selection/album-objective.ts:11-23`.

### 3.7 The framing tie-break shipped, never fired, and was deleted

The selection wiring required exact equality of a raw quality double before consulting
body coverage. Across 100,000 simulated near-duplicate pairs there were zero exact
ties. Byte-identical duplicates tie on both quality and keypoints, so framing still
cannot choose between them. The wiring was deleted; current selection explicitly does
not consult framing (`apps/mobile/src/selection/select-best-shots.ts:603-611`).

This does not reject pose features or M8's multi-person crop-readiness goal. It rejects
one inert consumer with an unreviewed full-body preference. Full evidence:
[`framing-tiebreak-measurement.md`](framing-tiebreak-measurement.md).

## 4. Reconciled sequence

The old linear sequence M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9 is retired.

1. **Close the remaining M0 evidence gap and diagnose M3's environment first.** The
   runtime/scheduling problem gates any honest candidate budget and full-library tier.
2. **Do the respecified M1 memory/storage work needed by M2.** Do not rebuild the
   already-fixed launch parse. Prefer the smallest safe lifetime-memory fix; adopt
   bounded binary storage when M2's durable signal/job queries require it.
3. **Build M2 durable signals and resumable priority jobs after the timing model is
   trustworthy.** Persistence prevents repeat work; it does not make a seven-hour
   first pass acceptable by itself.
4. **Build M5 hierarchy and measure candidate-budget curves only after M2 reuse and
   M3 throughput are available.** At 2.33 s/photo, K=24's proposed `5K=120` deep
   candidates cost about 280 s (4.7 minutes) if all are missing.
5. **Remove M4 and M6 from the delivery chain.** They are completed negative
   experiments, not prerequisites.
6. **Collect M7 preference data now; train later.** M7 does not depend on M6. Its model
   should start only when event-split labels and a checkpoint pipeline exist, and must
   beat the shipped zero-shot TinyCLIP axes plus rules.
7. **Prepare M8's group-photo truth set independently; integrate after M3 establishes
   a finalist latency/memory budget.** M7 is not a prerequisite for model acquisition
   or pose-recall measurement.
8. **Do M9 after M5 supplies durable moment/candidate structure and M7 supplies a
   measured preference signal.** Preference-event capture can continue meanwhile.

## 5. Milestone disposition

### M0 — audit, manifest, fixtures, standing gates

**Status: substantially built; one standing gate remains blocked.**

**Plan assumed:** active model paths, preprocessing, fixtures, and regression gates
were unknown.

**Measured:** the live model path and manifest are documented in
[`CX-21-PLAN-AUDIT.md`](CX-21-PLAN-AUDIT.md). Three pinned album fixtures exist. The
one-command CX-25 runner currently passes degradation monotonicity and frozen-pair
drift, but the eyes-open gate reports no eligible real ML Kit open/closed comparisons.

**Left to do:** add a real near-duplicate eye-state fixture set before treating 95% as
a metric; keep model manifests and timing instrumentation current; do not call M0 fully
closed while a standing gate is data-blocked. Reproduce with
`node apps/mobile/src/eval-gates/run-cx25-gates.ts`.

### M1 — bounded embedding storage and lifetime

**Status: OPEN, RESCOPED.**

**Plan assumed:** SQLite was needed mainly to remove a 6.7 s atomic launch parse and
int8 storage was new.

**Measured:** the launch parse was already split and reduced; the remaining parse
yields every 500 rows. Embeddings are already int8/base64 on disk, then expanded into
89.5 MB of `number[]` memory with no release path. Startup and the observations load
are different paths.

**Left to do:** define a bounded query contract (rows, bytes, latency, and peak/resident
memory), then choose the smallest safe solution: guarded release, typed/native
arithmetic, or SQLite BLOB queries. If SQLite is selected, retain the original parity,
idempotence, resume, source-fingerprint, and rollback requirements. Success is bounded
access and controlled lifetime, not “load every embedding in under 150 ms.”

### M2 — Tier A/B/C durable analysis and jobs

**Status: OPEN.**

**Plan assumed:** a new tiered queue could precede M3 and turn repeated 148 s work into
cached reads.

**Measured:** only cheap candidate probes persist. Pose, TinyCLIP outputs, ML Kit face
results, the perceptual fingerprint, and full pixel-quality results are recomputed and
discarded. The foreground/headless face scan is resumable via a cursor, but it is not
the proposed versioned deep-signal lease queue. See `CX-21-PLAN-AUDIT.md:16-18` and
`apps/mobile/src/selection/candidate-probe-cache.ts`.

**Left to do:** persist each deep signal by asset revision + model + preprocessing
version; add user-priority jobs, leases, cancellation, retries, dependency invalidation,
and process-death resume. Define Tier C by measured hours, charging-window assumptions,
energy, thermals, and completion rate. Sequence implementation after M3 exposes the
true per-photo cost, while doing only the storage foundation M2 actually requires.

### M3 — inference and runtime acceleration

**Status: OPEN, WITH THE PROPOSED QUANTIZATION RUNG REJECTED.**

**Plan assumed:** the 140 s stage was TinyCLIP kernel time; current-graph int8 or FP16
CPU would reach 0.8/1.4 s per photo.

**Measured:** the timing span includes scheduling and contention. Current-graph full
int8 cannot convert; mixed int8 is structurally fragmented and measured 3.4x slower on
Mac. The FP16-CPU behavior cannot be engaged through fast-tflite 3.0.1. MoveNet's
concurrent 1,111 ms span is above the 0.8 s target even with zero TinyCLIP time.

**Left to do:** isolate kernel time from JS scheduling by varying concurrency and
preprocessing placement on device; measure cold/warm init, true invoke time, thermal
behavior, and operator coverage; then test a cleanly reconverted graph or native LiteRT
runtime that exposes required delegate options and deterministic release. Preserve
fidelity and product-decision gates. Do not infer Android performance from the Mac
slowdown.

### M4 — multi-prototype identities

**Status: BUILT / MEASURED / REJECTED; FLAG OFF.**

**Plan assumed:** existing person tiles contained multiple time/view/lighting modes, so
one centroid sat between modes and caused fragmentation.

**Measured:** zero of 937 multi-face people fell below the calibrated intra-tile bar;
random partitions produced the same risk/reward curve; no policy gained a merge at safe
impostor budgets.

**Left to do:** nothing under the original activation milestone. Keep the reproducible
harness and flag off. Treat remaining identity fragmentation as separate measured work
(correction/review flow, false cannot-link evidence, timestamp quality, or a new
embedder). Rerun prototypes only after an input change that could make tiles genuinely
multi-modal.

### M5 — duplicate/burst/moment hierarchy and candidate budget

**Status: OPEN.**

**Plan assumed:** the current top-64 was quality-only and `clamp(5K, 96, 192)` could be
adopted after grouping.

**Measured:** the cap already has diminishing-return axes for time, place, BlurHash
content, and familiar people, so “add diversity” is not the task. It still operates
before durable moments and explicit reservations, so it can lose unique evidence.
The proposed budget is not currently affordable: 96–192 missing deep candidates at
2.33 s/photo are approximately 224–447 s (3.7–7.5 minutes).

**Left to do:** add durable exact/edited duplicate, burst, moment, and event hierarchy;
explicit reservations and entry reasons; representative/alternate behavior; and
coverage-recall fixtures. After M2/M3, sweep candidate count against latency, unique-
moment recall, and final-album changes. Select the smallest measured budget that
saturates value; do not canonize `5K` without that curve.

### M6 — constrained submodular selector

**Status: BUILT / MEASURED / REJECTED; PRODUCTION FLAG OFF.**

**Plan assumed:** facility location plus saturating coverage would materially improve
the shipped discrete-key greedy, and a DPP remained useful as an offline baseline.

**Measured:** facility location was nearly inert on all three fixtures; facility alone
degraded moment/people coverage on two; the duplicate improvement came wholly from the
hard constraint. The current blended similarity cannot be used as a valid DPP kernel.

**Left to do:** keep the shipped coverage selector and hard duplicate constraint. No
facility-location rollout and no DPP baseline are pending. Reopen only with broader
held-out events and a predeclared product metric capable of justifying added complexity;
a DPP additionally requires a provably PSD kernel construction.

### M7 — learned multi-output quality and aesthetic head

**Status: OPEN; LABEL CAPTURE PARTLY BUILT.**

**Plan assumed:** the baseline was hand-written rules with no aesthetic signal, and a
small learned head would follow M6.

**Measured:** the shipped TinyCLIP path already supplies zero-shot `aesthetic`,
`composed`, and `cleanFrame` axes; the planner weights them alongside objective
signals (`apps/mobile/src/ml/tinyclip.ts`, `apps/mobile/src/selection/album-planner.ts:122-133`).
The app now stores bounded, pseudonymous near-duplicate and album-edit pairwise labels,
but there is no trained multi-output checkpoint or held-out preference win
(`apps/mobile/src/selection/preference-label-store.ts:1-7`, `:225-280`).

**Left to do:** continue collecting real edits; freeze event-level train/validation/test
splits; build a reproducible training/checkpoint pipeline; measure pairwise ranking,
group lower-tail behavior, severe-defect non-inferiority, calibration/uncertainty, and
album preference. The baseline to beat is zero-shot axes + current rules, not rules
alone. M6 is not a prerequisite; M2's durable features are the useful dependency.

### M8 — multi-person framing and crop readiness

**Status: OPEN; THE EXACT-TIE CONSUMER IS REJECTED.**

**Plan assumed:** single-person MoveNet could be supplemented by multi-person pose or
segmentation for finalists, producing soft framing states and crop proposals.

**Measured:** no multi-person pose or segmentation dependency is installed. MoveNet is
single-person and remains useful for diversity. The shipped exact-equality framing
tie-break fired zero times in 100,000 pairs and was removed; that says nothing positive
or negative about multi-person recall or crop safety.

**Left to do:** acquire a real group-photo truth set; integrate and license a candidate
model; measure pose recall against face count, first-use/download failure, latency,
memory, masks, and fallback; persist aspect-specific crop candidates; verify important-
face retention. Run only on finalists within an M3-established budget. Never revive
the old raw-double tie gate or claim complete-body guarantees.

### M9 — story, personalization, and layout feedback

**Status: OPEN; FOUNDATIONS PARTLY BUILT.**

**Plan assumed:** chronological story roles, bounded local preference learning, and
layout-requested replacements would follow M7/M8.

**Measured:** the current planner presents selected photos chronologically, and the app
captures bounded pairwise preference events. Narrative roles/arc optimization, a
learned local preference update, disable/reset behavior, layout-driven replacement,
and held-out edit/reorder improvement are not implemented. `docs/selection-roadmap.md`
still lists narrative arc ordering as planned and Bradley–Terry learning as waiting for
enough events.

**Left to do:** keep collecting versioned decisions now. After M5 provides durable
moments and M7 proves a preference signal, add story roles with chronology as a hard
backbone, train the bounded local model with reset/disable, and define a typed layout-
replacement contract. Gate on held-out edits/reorders and album preference, not the
existence of a new ordering algorithm.

## 6. Section 22 — corrected targets

The old section mixed valid regression bars, unmeasurable aspirations, wrong baselines,
and targets below the current-path floor. This table is authoritative.

| Original target | Verdict | Replacement guidance |
|---|---|---|
| Embedding load `<150 ms` after M1, baseline 6.7 s | **Invalid baseline and wrong operation.** Launch no longer loads observations; loading all embeddings into JS contradicts bounded access. | Measure a specified query by rows/bytes, p50/p95 latency, peak memory, and retained memory. Launch must not parse observations. |
| Warm 3k build `≤5 s`; repeat `≤2 s` after M2+M5 | **Not an M5 gate and not currently grounded.** M5 cannot meet it while deep signals are missing and rerun. | Measure separately: zero-missing-signal query/selection time and builds with N missing signals. Set a wall target only after M2 persistence and M3 throughput are measured. |
| Deep signal `≤1.4 s` after FP16 CPU | **Unreachable by the named current integration.** Default XNNPACK does not execute fp16 arithmetic without `FORCE_FP16`, and fast-tflite 3.0.1 does not expose it. FP16 was not run. | First expose and verify the backend or use a different runtime; then set a target from a real device run. |
| Deep signal `≤0.8 s` after int8 | **Below the current-path floor.** `max(TinyCLIP, MoveNet)` is the concurrent wall; `max(0, 1.111 s) ≈ 1.111 s > 0.8 s`. Current-graph TinyCLIP int8 is also rejected. | A sub-0.8 s goal requires changing pose/runtime/environment as well as TinyCLIP. Replace the target only after isolated device measurements. |
| Face pair-AUC drop `≤0.2` percentage point | **Usable regression bar once the labelled set and previous result are frozen.** | Keep with pair crossings and category metrics; never substitute it for impostor-merge counts. |
| Degradation violations `<2%` | **Usable standing gate.** | Keep the denominator and vacuity guard visible. The current deterministic fixture runner passes; expand real coverage without silently changing the bar. |
| Eyes-open ordering `≥95%` | **Currently unmeasurable, not an open performance question.** The checked-in corpus has zero eligible real ML Kit open/closed comparisons. | Gate only after a minimum eligible real-pair denominator is checked in; until then report **BLOCKED**, never 0% or 100%. |
| TinyCLIP NN recall@10 `≥0.98` | **Valid initial fidelity gate, not a speed target.** | Retain alongside p1/p5 agreement, pairwise similarity ordering, threshold shifts, and album changes. It cannot rescue a structurally slow conversion. |
| Tier-C backfill “within a few charging sessions” | **Not a numeric gate.** “Few” and session duration are undefined, and the proposed scheduler is not built. | Specify library size, device class, charging hours, thermal/battery conditions, interruptions, completion rate, and energy before choosing a threshold. |
| Key-person fragmentation `≥60%` reduction vs 2,237 clusters, zero impostor merges | **Dimensionally invalid.** 2,237 is total clusters, not key-person fragmentation, and no frozen key-person truth set defines the numerator. | Name the key people; freeze pair/cluster truth; report B-cubed and per-person fragments plus auto-confirmed impostor merges. Set a reduction target only from that baseline. |

## 7. Do-not-redo list and caveats

Do not repeat these as though they are untested:

- mixed-int8 conversion of the shipped TinyCLIP flatbuffer;
- full-int8 conversion of that graph without addressing its `DIV`/LayerNorm structure;
- FP16 CPU through the current fast-tflite API;
- multi-prototype activation on the current owner library;
- facility-location rollout on the current three fixtures;
- a DPP over the current hand-blended similarity matrix;
- an exact-raw-double framing tie-break.

Do not overread the negative results either:

- The int8 slowdown is a Mac measurement; Android/ARM latency was not measured.
- FP16 CPU was never run; only the current access route was ruled out.
- DPP was rejected for lack of a valid kernel, not by an album preference A/B.
- Multi-prototype identity lost on one library/model whose existing tiles are already
  coherent; another distribution could differ.
- The current timing spans contain environment delay; they are not kernel benchmarks.

## 8. Evidence and reproduction index

- Repository audit and original section-22 critique:
  [`CX-21-PLAN-AUDIT.md`](CX-21-PLAN-AUDIT.md)
- Device timing, concurrency correction, quantization result, and graph structure:
  [`DEEP-ANALYSIS-TIMING.md`](DEEP-ANALYSIS-TIMING.md)
- Embedding representation and lifetime:
  [`EMBEDDING-MEMORY.md`](EMBEDDING-MEMORY.md)
- Multi-prototype implementation and measurement:
  `apps/mobile/src/faces/face-prototypes.ts`, `scratch/multi-prototype/measure.ts`
- Submodular objective, production-off flag, and A/B:
  `apps/mobile/src/selection/album-objective.ts`,
  `apps/mobile/src/selection/album-planner.ts`,
  `apps/mobile/src/selection/album-selector-ab.ts`
- Framing tie-break measurement and deleted consumer:
  [`framing-tiebreak-measurement.md`](framing-tiebreak-measurement.md),
  `apps/mobile/src/selection/select-best-shots.ts:603-611`
- Current standing gates:
  `apps/mobile/src/eval-gates/run-cx25-gates.ts`
