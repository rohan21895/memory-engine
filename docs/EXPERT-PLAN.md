# Photeo — Implementation Plan for Claude Code

> Received from an external expert review, 2026-08-27. Verbatim. Our analysis of
> it, and what we actually intend to build, lives in `docs/EXPERT-PLAN-REVIEW.md`.

Photeo is an Android app (React Native + TFLite) that privately scans the phone's photo
library, groups people by face, understands events and trips, and builds a small,
beautiful, socially presentable album for any filter the user chooses — entirely
on-device, nothing uploaded.

The core product distinction: **photo scoring decides whether an individual photograph
is good; album optimization decides whether that photograph adds value to this
particular collection.** An excellent portrait can be omitted because the album already
has three similar ones; a slightly imperfect photo can be kept because it is the only
image of an important person, place or moment.

---

## 1. How Claude Code must use this plan

Do not implement the whole plan in one pass. The workflow is:

```text
read this plan once → inspect the actual repository → implement only the
CURRENT PHASE (section 20) → run the required parity/performance tests →
report measured results and blockers → the phase pointer is advanced for the next run
```

Ground rules for every phase:

- Inspect the repo before naming files, packages, RN versions, native-module
  architecture, worker classes or database libraries. Report the actual files,
  data flow, JS/native boundary and covering tests before coding.
- Treat every number in section 3 as the measured baseline, not an estimate.
- Never silently change a model, preprocessing step, threshold or selection weight
  while doing a storage or infrastructure migration.
- Never fabricate model weights, benchmark outcomes or accuracy claims. Never ship
  random/untrained weights as a "model". If an experiment can't run (missing
  checkpoint, dataset, device), build the reproducible harness, document the exact
  blocker, and leave production behaviour unchanged.
- Every behaviour change lands behind a feature flag with the legacy path retained
  until parity is demonstrated. Every expensive operation is resumable; every derived
  result is versioned.
- No magic numbers: thresholds, weights, budgets and windows live in versioned
  configuration (section 21) and are logged with results.
- No per-photo or per-face work crosses the RN bridge item-by-item. Embeddings never
  materialize as JS number arrays or JSON/base64 — ArrayBuffers/typed arrays only,
  similarity math in native code (C++ JSI or batched Kotlin).
- Use transactions for multi-row writes; commit job results and job completion
  together where feasible.
- Extend the existing stage timers (`cache-load`, `candidate-probe`,
  `candidate-rank`, `deep-analysis`) to every new stage.
- End each phase with a completion report: what changed, measured before/after,
  gates passed/failed, open blockers.

---

## 2. Non-negotiable invariants

**On-device privacy.** Photos, thumbnails, face crops, face and semantic embeddings,
pose descriptors, clusters, events, scores, selections and preference data never leave
the device. No cloud inference, no remote search, no analytics or crash logs containing
image paths, vectors or full DB rows. Face embeddings are sensitive derived biometric
data: app-private storage, never logged, invalidated on permission revocation or user
reset, never used for authentication or demographic inference. Downloading approved
model files is allowed; it must never involve uploading user content.

**Determinism.** Same library, asset revisions, model versions, preprocessing versions,
configuration and user constraints → same clusters and same album. All tie-breaking is
deterministic (stable asset ID). Deliberately stochastic experiments record their seed.

**Incrementality.** A new photo must not rebuild the library. A model update
invalidates only its own outputs and dependents. A changed photo invalidates only
itself.

**Explainability (dev builds).** For each selected photo the system can answer: base
quality, coverage added (moment/location/people/shot type), which duplicate group it
represents, which similar photos it beat, why a higher-scoring photo was omitted, which
constraints applied.

**Graceful uncertainty.** Uncertain faces stay unassigned ("Unsorted"), framing can be
`UNKNOWN`, event boundaries carry confidence, quality carries uncertainty, a person
merge can stay pending. Never convert ambiguity into false certainty.

---

## 3. Measured baseline (owner's phone and library)

| Metric | Value |
|---|---:|
| Photos indexed | 11,853 |
| On-device storage | 40 GB (mean 4.7 MB/photo) |
| Faces detected | 17,766 |
| Person clusters | 2,237 (932 with 2+ faces) |

Family library: many group photos, a small recurring cast, visually similar relatives,
and an infant photographed across the first two years. False identity merges are
maximally damaging here; one-vector identities are unsuitable.

Current pipeline: ML Kit face detection (box, landmarks, eye-open, Euler angles);
`w600k-mbf` 512-d embeddings (13.6 MB fp32); average-linkage clustering, one centroid
per person, assignment bar 0.449, merge bar 0.600; same-photo cannot-link with a 0.72
similarity escape; MoveNet Lightning single-pose (2.9 MB int8); TinyCLIP ViT-8M/16
(33.2 MB fp32); 76-value perceptual fingerprint, collapse ≥0.92; hand-crafted quality
rules; global top-64 prepass → ~24 selected; storage in `face-index.json` (2.5 MB) +
`face-observations.jsonl` (13.8 MB); inference via `react-native-fast-tflite`,
XNNPACK CPU only. Verify in the audit whether both bundled face models
(`mobilefacenet-192` and `w600k-mbf`) are active before touching either.

Timings (3,000-photo event): cache load 758 ms; candidate probe 3,263 ms; candidate
rank 2,368 ms; deep analysis ~140 s for 64 photos (~2.2 s/photo); cold album 148 s;
repeat 26 s. JSONL: 137 ms disk read but **6,694 ms atomic Hermes `JSON.parse`**.
Full-library deep analysis at current speed ≈ 7.2 h.

Identity threshold evidence (co-occurrence as known-different-person labels):

| Merge bar | Impostors admitted | Real merges gained |
|---:|---:|---:|
| 0.60 | 8 | 0 |
| 0.50 | 40 | 8 |
| 0.45 | 59 | 106 |
| 0.40 | 80 | 326 |

Genuine splits sit at 0.50–0.52 average linkage. A single global threshold cannot fix
fragmentation without admitting impostors — the representation must change (section 9).

---

## 4. Architecture decisions

1. **Storage:** SQLite with binary BLOB embeddings replaces whole-file JSON/JSONL.
2. **Analysis timing:** compute each expensive signal once per (asset revision, model
   version, preprocessing version), progressively in tiers — not everything at first
   run, and never at album time only.
3. **Inference:** benchmark matrix before any runtime switch; quantization ladder from
   lowest risk upward; face-embedding changes gated by a labelled verification
   benchmark; thresholds recalibrated after any model change.
4. **Identity:** 1–6 quality-controlled, time-diverse medoid prototypes per person;
   strict same-photo cannot-links with **no similarity escape**; robust multi-evidence
   merges; temporal chaining for infant appearance drift.
5. **Candidates:** remove the global top-64; build candidates from moments and
   coverage first, quality fill second; budget ≈ 5× album size.
6. **Selection:** constrained submodular (facility-location + saturating coverage)
   with lazy greedy and bounded swaps; DPP only as an offline baseline.
7. **Quality:** a multi-output learned scorer plus objective signals — never one
   opaque beauty score; hand-crafted rules become features/soft penalties except a
   small high-confidence exclusion set.
8. **Framing:** cheap pass for all photos; multi-person pose/segmentation only for
   finalists; framing is a confidence state, never a guarantee.
9. **Licensing:** out of scope for this internal prototype; record provenance where
   trivially available; a separate release gate handles model/dependency/training-data
   licensing before any commercial release.

---

## 5. Storage

Choose the SQLite library in the M0 audit against hard requirements — JSI-based,
ArrayBuffer/typed-array BLOB reads, prepared statements, transactions, no
multi-megabyte JS materialization. op-sqlite, react-native-nitro-sqlite and
expo-sqlite are all acceptable if the repo supports them. Similarity math stays native:
brute-force NEON dot products over fp16 BLOBs are <10 ms at 17.8k × 512 — no vector
extension.

Schema sketch (adapt names to repo conventions; every derived row carries asset
revision + model + preprocessing versions):

```sql
photos(id PK, uri, taken_at, time_confidence, lat, lon, width, height, subtype,
       asset_revision, phash BLOB, clip BLOB /* fp16*512 */, scene_tags,
       technical JSON, quality JSON, sig_ver_core, sig_ver_clip, sig_ver_quality)
faces(id PK, photo_id FK, bbox, landmarks BLOB, eye_open, smile, yaw, pitch, roll,
      quality REAL, quality_tier, emb BLOB /* fp16*512 */, align_transform,
      prototype_id, identity_id, observation_type, sig_ver_face)
prototypes(id PK, identity_id, medoid_face_id, centroid BLOB, t_min, t_max, support_n)
identities(id PK, name, aliases, clustering_version, flags)
constraints(a, b, kind /* must|cannot|chain */, score, source, evidence, immutable)
dupe_groups(photo_id, group_id, kind /* exact|edited|burst */, similarity)
moments(id, event_id, t_start, t_end, importance)
events(id, t_start, t_end, geo_cluster, label, importance)
analysis_jobs(id PK, photo_id, signal_type, priority, state, lease_owner,
              lease_expires_ms, attempt_count, available_after_ms, updated_at_ms)
selections(album_id, photo_id, explanation JSON, selector_config_version)
annotations(id, mode, payload JSON, created_at)
config_versions(id, kind, json, created_at)
```

Binary rules: embeddings are fp16 BLOBs end to end; a sampled round-trip parity check
(~100 cosines pre/post) guards the codec.

**Migration:** incremental, idempotent, resumable importer from `face-index.json` +
`face-observations.jsonl` with a source fingerprint; dual-read validation mode; photo,
person and face counts must match; existing IDs and cluster assignments unchanged;
fixture album outputs unchanged; startup no longer parses the JSONL; legacy files
retained behind a debug flag for rollback.

---

## 6. Progressive analysis

At 2.2 s/photo, mandatory full first-run analysis ≈ 7 hours — unacceptable. Use
persistent progressive computation:

- **Tier A — initial indexing (cheap):** MediaStore metadata + revisions, small
  thumbnail, perceptual fingerprint, face detection + alignment + embedding, exposure
  histogram, global and face-region sharpness, screenshot/document metadata. Resumable,
  with visible progress.
- **Tier B — selected-event priority:** when the user picks a filter, enqueue that
  universe's missing deep signals at top priority — semantic embedding, quality
  features, pose, group lower-tail face quality, crop flexibility, framing, optional
  finalist segmentation. Show a preliminary album that improves as signals arrive, or
  wait for a bounded high-priority set (product-test which).
- **Tier C — background backfill:** opportunistic under charging/battery, thermal,
  execution-window, storage and no-pending-user-work conditions. Correctness never
  depends on Android granting unlimited background time.

Priority classes (higher wins; ties by capture time then stable ID):

```text
1000 user-requested finalist verification   900 user-requested candidate deep analysis
 800 visible people-cluster repair          700 newly captured photo Tier A
 500 recently viewed event backfill         300 general charging backfill
 100 maintenance/reindex
```

Job leasing (atomic; adapt if `RETURNING` unavailable):

```sql
UPDATE analysis_jobs SET state='RUNNING', lease_owner=?, lease_expires_ms=?,
  attempt_count=attempt_count+1, updated_at_ms=?
WHERE id IN (SELECT id FROM analysis_jobs
             WHERE state IN ('PENDING','RETRY')
               AND (available_after_ms IS NULL OR available_after_ms<=?)
             ORDER BY priority DESC, id ASC LIMIT ?)
RETURNING *;
```

Expired leases return to retryable. A job is complete only if a row exists for
(photo_id, asset_revision, signal_type, model_id, preprocessing_version); re-runs
no-op or replace transactionally.

Dependency invalidation (explicit in code):

```text
asset revision changed   → fingerprint, faces, semantic, pose, quality, moments, albums
face detector changed    → face observations, embeddings, identities, person features, albums
face embedder changed    → embeddings, prototypes, identities, person-based albums
semantic model changed   → semantic embedding, semantic moments, learned heads, albums
quality model changed    → quality outputs and albums only
selector config changed  → album builds only
```

Keep prior-version outputs temporarily for A/B and rollback.

---

## 7. Decode and preprocessing

Decode once per photo per batch at target size (`ImageDecoder`/`inSampleSize` — never
full 4.7 MB decode then downscale); derive face-detector input, face crops, semantic
input, pose input, quality regions and thumbnail from the one working bitmap; release
buffers; never hold dozens of large bitmaps. Resolution tiers: metadata-only →
224–320 px model input → 512–768 px working image → native-resolution patches for
finalist verification only.

One canonical orientation: all boxes/landmarks stored normalized to the correctly
oriented image; test all 8 EXIF orientations; fingerprints orientation-invariant or
consistently canonicalized. Every model has an immutable preprocessing contract (colour
space, resize, aspect policy, channel order, pixel range, normalization, output L2) —
a model ID is incomplete without its preprocessing version. Never stretch photos square
for composition scoring; letterbox with padding features, multi-crop, or trained-for
centre crop. Track native memory (bitmaps, tensors, ByteBuffers, JSI ArrayBuffers);
buffer pools only after correctness tests.

---

## 8. Inference, quantization and acceleration

TinyCLIP fp32 on XNNPACK CPU is the dominant cost. The question is not "GPU or INT8?"
but which model/runtime combination wins end-to-end latency, memory, thermal and
fidelity across supported devices. Build a narrow native benchmark module first; do not
rewrite inference wholesale.

**TinyCLIP benchmark ladder** (cheapest, lowest-risk first; identical semantics and
preprocessing across variants):

```text
1. FP16 weights, XNNPACK CPU   (ARMv8.2 half precision — verify the fp16 path engaged)
2. Dynamic-range INT8, CPU
3. Full INT8, CPU              (broad-device production candidate)
4. FP16, GPU delegate
5. Native LiteRT CompiledModel prototype (CPU/GPU)
6. Optional NPU/QNN tier on qualifying SoCs
```

For every variant report **cold delegate init, warm init (with kernel-cache
serialization dir + model token), first inference, steady inference** — separately.
Delegate serialization fixes initialization, not steady-state cost. Record delegated
operator coverage; partial delegation can be slower than CPU. Never claim a backend is
active because delegate creation succeeded — confirm the execution plan and outputs.

Full-integer calibration: 500–2,000 inputs through the exact production preprocessing,
stratified across indoor/outdoor, day/night, portrait/couple/group, infant, landscape,
food/objects, screenshots, saturated edits, dark/noisy, panoramas, multiple cameras.

Fidelity gates (never mean cosine alone — small average drift can still reorder close
neighbours): mean and p1/p5 cosine vs fp32; nearest-neighbour recall@1/5/10 (initial
gate: recall@10 ≥ 0.98 on the local benchmark, to be validated); Spearman of pairwise
similarities; near-duplicate group changes; moment clustering changes; candidate-pool
and final-album overlap (reported, not required identical); A/B album non-inferiority
before a production switch.

**Face embedding quantization** is more sensitive — thresholds and cluster geometry
depend on it. Order: fp32 baseline → fp16 → dynamic-range/weight-only with float I/O →
full INT8 only after the labelled verification benchmark (section 18) exists. Always:
identical crops and alignment, dequantize output, **L2-normalize in float**, recompute
similarity distributions, recalibrate 0.449/0.600, re-run cluster evaluation. No
universal accuracy claim substitutes for the owner's close-relative and infant pairs.

Runtime selection in production: device capability profile (SDK, SoC, accelerators,
variant support, self-test result, warm latency class, thermal/memory class) with a
first-launch self-test, cached; fallback order validated-NPU → validated-GPU-fp16 →
validated-CPU-int8 → current CPU float; crashing/invalid backends quarantined per
device/runtime/model version. QNN/NPU is an optional high-end tier — never a
prerequisite for acceptable performance. Keep long-lived model instances (never load
TinyCLIP 64 times); bounded instance pool; batch at application level (load once,
run N prepared inputs, reuse buffers, commit outputs in one transaction).

Preferred integration: a thin native module on LiteRT for the two heavy models rather
than perpetually patching `react-native-fast-tflite`; a patched-library experiment only
if the audit justifies it.

---

## 9. Faces and identity

**Detection and alignment.** ML Kit supplies box, landmarks, Euler angles, eye-open and
smile — reliable only for near-frontal faces; off-angle/unavailable classification is
`UNKNOWN`, never "closed" or "no smile". Document the exact current alignment pipeline
before changing anything; store alignment transform, confidence, crop padding and
preprocessing version. **An alignment change is a face-model change** and forces
threshold recalibration.

**Face quality** is a vector (pixel size, relative area, sharpness, exposure,
occlusion, landmark confidence, yaw/pitch/roll, truncation, noise, alignment residual,
eye-state availability) combined into `faceQuality` with components preserved. Tiers:

```text
SEED_QUALITY        can create/represent a prototype
ASSIGNMENT_QUALITY  can be assigned to an established identity
DISPLAY_ONLY        shown, but never automatic identity evidence
REJECTED            likely false detection / unusable crop
```

Store all faces per photo (group shots: importance from area, centrality, sharpness,
event importance, foreground association, truncation; background faces count for
context, not quality gating). Flag likely false detections (posters, statues,
reflections, photo-of-photo) with an observation type; keep them out of ordinary
clusters.

**Multi-prototype identities.** One centroid cannot represent a growing infant or
profile/lighting modes — the mean lands between modes and matches none. Represent each
person as 1–6 **medoids** (real, auditable face crops), count tiered by size:
1–2 reliable faces → 1; 3–7 → up to 2; 8–20 → up to 4; 21+ → up to 6. Selection:
filter to SEED_QUALITY; pick highest quality first; then greedily maximize weighted
distance from existing medoids with bonuses for uncovered time buckets and view/
lighting novelty; reject unsupported outliers. Infant identities get explicit temporal
coverage buckets (0–3, 3–6, 6–9, 9–12, 12–18, 18–24, 24–36 months of that identity's
observed timeline — temporal representation, never age classification from the face).

**Face→person score:** `s(f,P) = max over prototypes cos(f,p)`; optionally blend top-2
prototype similarities when the second is independently supported. High-quality frontal
faces use the normal bar; low-quality/profile faces require *stronger* evidence or stay
uncertain — never a lowered bar. Assignment uses high/grey/low bands; grey stays
unassigned or pending.

**Merges** never use a single average over all pairs. A merge is eligible only if:

```text
no immutable cannot-link and no ordinary same-photo conflict
≥2 supporting pairs above pairSupportThreshold, from ≥2 distinct source photos
at least one reciprocal nearest person/prototype pair
robust top-k score above mergeThreshold, with a quality minimum
```

**Temporal chaining** bridges infant drift: 3 mo ↔ 5 mo ↔ 8 mo ↔ 12 mo ↔ 18 mo — local
reliable matches across adjacent windows link sub-clusters a direct 3↔20-month
comparison never could. Chaining is supporting evidence only; it never overrides
cannot-links or close-relative caution. Config seeds: window 45 days, chain pair bar
0.52, ≥2 supporting pairs from ≥2 photos. Chain-only merges land in a suggested-merge
confirmation queue until measured precision justifies auto-merge.

**Same-photo rule:** two distinct faces in one ordinary photo are **strictly
cannot-linked — remove the current `unless similarity ≥ 0.72` escape.** High similarity
between co-occurring relatives is exactly the failure mode to prevent. Until dedicated
mirror/collage/photo-of-photo/duplicate-face detectors exist, log high-similarity
same-photo pairs for developer review and keep the cannot-link; each future exception
requires a stored reason and evidence. Accepted, audited side effect: genuine mirror
shots stay split until those detectors ship. Don't materialize inherited cannot-links
quadratically — store observation-level constraints and test membership pairs at merge
time (compact conflict sets per identity, rebuilt transactionally on merge).

**User corrections** ("same person", "different people", "move face", "remove face")
are high-priority immutable constraints; confirmed merges preserve source aliases.

**Splitting:** detect over-merges via internal same-photo conflicts, disconnected
prototype graphs, bimodal structure, unsupported prototypes, impossible co-occurrence;
keep the stable ID on the largest/user-labelled component.

**Rollout:** shadow mode. Keep `legacy-centroid-v1` while `multiprototype-shadow-v1`
runs in parallel; compare false merges/splits, B-cubed P/R/F1, key-person
fragmentation, unassigned rate, same-photo violations; inspect key relatives; then
activate. **False merges are weighted more severely than false splits.**

---

## 10. Duplicates, bursts, moments, events, trips

Hierarchy and default album behaviour:

```text
exact/edited duplicate group → max 1        ordinary burst → max 1 (sometimes 2)
important moment → 1–3 by diversity         major event/day → minimum coverage
trip → cover days, places, companions
```

Three distinct concepts, three detectors: **exact** (size/metadata prefilter → decoded
thumbnail hash → lazy cryptographic hash; don't hash 40 GB at first run), **edited**
(orientation-normalized fingerprint + current 76-value descriptor + semantic embedding
+ colour histogram + local-feature verification for ambiguous pairs; keep the 0.92 bar
until a labelled benchmark exists; store continuous similarity, not just group ID),
**burst** (time gap, same people, face positions, semantic + pose similarity, device,
GPS continuity — high semantic similarity alone is insufficient: two views of one
monument are meaningfully different). Group via candidate edges (time windows +
fingerprint neighbours) then verify; use connected components only where transitivity
is safe, else complete-link, to avoid chaining distinct views. Representative
selection within a group uses subject/face sharpness, eyes-open for important frontal
faces, expression, truncation, motion blur, exposure, composition, pose, memorability,
crop flexibility — alternates stay in the DB for instant one-tap replacement.
Live/Motion Photo best-frame extraction is a later milestone; model storage so one
logical asset can hold multiple frame candidates.

**Moments/events:** sort by corrected capture time; per adjacent pair compute log time
gap, day boundary, GPS distance, implied speed, location-cluster change, semantic
distance, person-set overlap, near-dup similarity, shot-type transition, source/device
change; boundary probability from a versioned heuristic first (logged feature
contributions), a small logistic/GBM once labels exist; segment with smoothing
(DP/Viterbi/PELT) — never split on one gap alone. Location: metric conversion, stay
points, clusters, travel transitions; infer the home region only when permitted, keep
it coarse. Time confidence handles WhatsApp/download timestamps, scans, screenshots of
old photos, timezone and clock errors — one suspicious timestamp must not distort an
event. Event importance: photo count, distinct moments, important people, location
rarity, action/emotion, favourites, user focus. The user's filter (date/location/
person/album/auto-event) defines the candidate universe; moments are built within it.

---

## 11. Per-photo signals

Persist independently versioned families: metadata; duplicate features; face
observations; semantic embedding; composition; technical quality; portrait/group
quality; pose; shot type; utility probability; memorability; framing/crop flexibility;
sensitivity flags. Cheap technical signals (luminance stats, clipping ratios, contrast,
saturation, Laplacian/Tenengrad sharpness globally and per face region, noise,
blockiness, entropy, horizon) are computed in Tier A — never summed raw across scales;
normalized per content class, weighted later. Regional quality evaluates where viewers
look (important faces, main subject, foreground, proposed crop): a soft background
face in a group portrait is not a soft central face.

The TinyCLIP embedding is stored once, normalized, and reused for similarity, scene
grouping, diversity, utility features and learned heads — derived classifiers never
rerun the backbone. **No text tower ships:** shot-type and concept features come from
concept vectors computed offline with the exact backbone + preprocessing version and
bundled as versioned constants (screenshot, document, receipt, whiteboard, meme, wide
establishing, landscape, architecture, food, group, couple, selfie, pet, action, night,
indoor, outdoor); regenerate on any backbone/preprocessing change. Shot type is
multi-label. Person-set representation: weighted (person_id, importance, confidence,
quality), with unknown faces kept as unknowns for layout comparison. Pose descriptor:
hip/torso-centred, scale-normalized, careful mirror handling, joint angles, body
orientation, face yaw, visibility mask — compact descriptor plus raw landmarks.
Memorability is contextual value (rarity of location/person-combination/scene, moment
importance, position in event arc, favourites, only-photo-of-X) — not a second
aesthetic score. Sensitivity: rely on existing hidden/secure-folder metadata and user
exclusions; no invented sensitive-content classifier.

---

## 12. Quality and aesthetic scoring

Rules handle severe defects; they cannot rank two acceptable photos by composition,
expression and social appeal. The scorer outputs **separate calibrated heads**:

```text
technicalQuality  aestheticQuality  portraitQuality  groupQuality
memorability      utilityProbability  cropFlexibility  uncertainty
```

Baseline model: small MLP on the semantic embedding plus objective features (technical
stats, face count, largest face area, weighted face-quality mean and lower tail,
eye-state, yaw distribution, pose/framing, shot type, subject area, crop flexibility,
utility signals). Architecture sketch: normalize → Dense 512/256 + GELU + dropout →
Dense 128 + GELU → independent heads.

**Training path (in order):**

1. *Public bootstrap:* compute embeddings for AVA (fallbacks TAD66K, PARA) through the
   exact production TinyCLIP preprocessing and train the heads there. Existing
   "improved aesthetic predictor" weights are OpenAI ViT-L/14-based and **cannot** be
   reused on TinyCLIP features — train from scratch. This satisfies the
   no-untrained-weights rule before local labels exist.
2. *Owner labels:* fine-tune on annotation-tool output (section 18) — burst winners
   and pairwise preferences with Bradley–Terry/logistic ranking loss, listwise loss
   within bursts, regression for technical attributes, binary utility. **Split by
   event**, never randomly, so burst near-duplicates don't leak between train and
   validation.
3. *Local personalization:* `q_user = q_global + wᵤᵀx` with bounded, regularized
   pairwise online updates `P(i≻j) = σ(wᵤᵀ(xᵢ−xⱼ))`; optional per-context profiles
   (portrait / family event / travel / print / social) only once enough feedback
   exists; reset and disable controls; no full-model on-device fine-tuning.

Never train on raw like counts — likes encode audience size, timing and platform
ranking, not photo quality. Pairwise preferences beat 1–10 scores.

**Content router** blends head weights by class (portrait/couple/group/child/
landscape/architecture/food/pet/action/night/utility): portraits weight expression and
face sharpness; groups weight the lower tail; landscapes weight composition; action
tolerates slight motion blur; infant memories let memorability outweigh minor flaws.
Group quality starting hypothesis (to validate):

```text
Q_group = 0.55·weightedMean(q_f) + 0.30·weightedP20(q_f) + 0.15·importantMin(q_f)
```

with weights by face area, centrality and event importance.

**Soft penalties** for moderate flaws (partly closed eyes, slight blur, small cut face,
background softness, minor exposure, off-angle); **hard exclusion only** for corrupt
files, non-representative exact duplicates, near-black frames, high-confidence
utilities when excluded, severe failures with no unique coverage, and user-hidden
content. A unique memory survives moderate flaws. Uncertainty is emitted at minimum
when content class is unknown, important faces are tiny, input was heavily
padded/cropped, or signals disagree. **High-resolution verification** runs on
finalists only (eye focus, motion blur, artifacts, texture, print resolution,
clipping); a failing finalist is replaced from the same moment.

---

## 13. Multi-person framing and crops

Two stages: cheap pass for all (ML Kit faces + MoveNet single pose + box heuristics);
deep pass for high-value candidates only. Deep-stage benchmark, three candidates:

1. MediaPipe Pose Landmarker (IMAGE mode, `numPoses = clamp(faceCount, 1, max)`,
   masks only for the framing experiment, lite tier first) — measure size, init,
   latency at 1/2/4/8 people, pose recall vs face count, memory with masks.
2. ML Kit subject segmentation — multi-subject masks, but beta and unbundled
   (Play-services download): handle first-use unavailability; optional path with
   fallback only.
3. Bundled MediaPipe selfie segmentation (general model, ~250 KB) — the guaranteed
   offline baseline mask for border-contact features.

Framing features: head/hair proximity to top edge, face box crossing edges,
shoulder/torso visibility, wrist/ankle endpoint confidence near edges, mask touching
frame, body-box truncation, crop-safe margin. Face↔pose association via head-region
containment, geometry, scale, Hungarian assignment. Output is a state, never a
guarantee:

```text
COMPLETE | LIKELY_TRUNCATED | UNKNOWN
```

Framing feeds **soft penalties** into portrait/group quality — never hard gates
(measured hard gates cost 2.7–13.8% of good selections on group-heavy libraries).
Crop proposals per aspect (original, 1:1, 4:5, 16:9, 9:16, print ratios) scored by
face/limb retention, saliency, headroom, edge safety, post-crop composition, remaining
resolution, horizon; stored as normalized rectangles. cropFlexibility = best score per
ratio + usable-ratio count + hero/spread/pairing suitability. Print readiness from
crop pixel dimensions vs target DPI, profile-defined, never hardcoded.

---

## 14. Candidate generation

The global top-64 prepass destroys coverage before the selector runs — it can discard
the only photo of a day, the only establishing view, a rare relative, the only action
shot, a unique-but-soft infant moment. No optimizer recovers what the prepass dropped.

Construction order for album size K:

```text
filter universe → remove inaccessible/excluded → exact/edited-dup + burst groups
→ moments → rank within groups → per-moment reservoirs → coverage reservations
→ global quality fill → deep-analyse missing signals → re-rank → selector
```

Budget `B = clamp(5K, 96, 192)`, configurable cap 256 — applied **after** cheap
duplicate collapse and moment formation. Per-moment reservoirs: ordinary 2; important
3–5; large group portraits extra alternates (eyes/expressions vary); unique moments ≥1
even at moderate quality — and keep *typed* candidates (best technical / best
portrait-group / best composition / most memorable / most crop-flexible) so one scalar
can't hide a photo that is best for a specific role. Reservations (candidate
guarantees, not final-selection guarantees) for significant days, important locations,
high-importance people, rare person combinations, establishing shots, activity/candid,
details, closing images. Person importance within the event: frequency across moments,
face area/centrality, key-moment appearances, user filters/labels — background
strangers get no quota. Flexible quality floor: severe failures out; moderate failures
stay when uniquely covering; floor scales with album size. Missing deep signals ≠ zero
quality: estimate from cheap signals + uncertainty penalty + priority job; when latency
matters, wait only for the most promising missing candidates. Record entry reasons
(`BEST_IN_BURST`, `MOMENT_RESERVE`, `DAY_RESERVE`, `LOCATION_RESERVE`,
`PERSON_RESERVE`, `SHOT_TYPE_RESERVE`, `GLOBAL_QUALITY_FILL`, `MEMORABILITY_RESERVE`,
`USER_FAVOURITE`) as a compact bitset.

---

## 15. Album selection objective

Submodularity matches the product: the first good photo of a person is valuable, the
sixth near-identical portrait adds almost nothing; covering a new moment is valuable,
repeated coverage saturates. Production objective:

```text
F(S) = λq·Q(S) + λf·FL(S)
     + Σ_categories λc·Cc(S)      over moment, people, location, day, shot, role, pose

Q(S)  = Σ_{i∈S} w_i·q_i
FL(S) = Σ_{u∈U} a_u·max_{i∈S} sim(u,i)          (facility location)
Cc(S) = Σ_{g∈category} ω_g·h(count_g(S)),  h concave:
        h(n) = 1 − e^(−τn)   or piecewise 0 → 1.0 → 1.45 → 1.65 → slow saturation
```

Similarity blend (v1 config, logged per component, non-negative and consistently
scaled for FL):

```text
sim(i,j) = 0.30·semantic + 0.18·people + 0.15·pose + 0.12·composition
         + 0.10·location + 0.10·moment + 0.05·colour

people: weighted Jaccard  Σ min(w_ip,w_jp) / Σ max(w_ip,w_jp)
pose:   descriptor similarity for solos; assignment-matched layout sets for groups;
        fall back to box layout + higher uncertainty when pose is missing
```

Hard constraints: |S| = K; max 1 per exact/edited duplicate group; exclude
inaccessible/hidden/user-excluded; hero-slot minimum resolution. Configurable
hard-or-soft: max 1 per ordinary burst; max 2 per ordinary moment; person-dominance
cap; **minimum coverage for explicitly user-selected people**; major-day coverage —
soft when hardness would make the problem infeasible, with a documented relaxation
order.

Algorithm: **feasible lazy greedy** — priority queue of marginal-gain upper bounds;
pop, skip if a hard constraint blocks, recompute true gain, accept if still ≥ next
bound else re-push; cache category counts and FL maxima so marginals are O(small);
deterministic ties by stable ID. Then a **bounded 1-swap pass** (plus targeted
2-for-1 around missing coverage), keep swaps improving by >ε, log each. Guard against
diversity-at-any-cost: base quality term + uniqueness-scaled quality floor + moment
importance + finalist verification — the user should see diversity among *good*
photographs.

Persist a selection explanation per photo (baseQuality, marginalGain, coverage added,
duplicate group, represented-candidate count, nearest-selected similarity, constraints)
and support a debug query for omitted candidates (best substitute, redundancy,
violated constraint, terminal marginal gain).

DPP remains an **offline baseline only**, compared on album-level human preference
against MMR, the current heuristic, and submodular greedy ± swaps. A valid DPP kernel
must be PSD — `L = diag(q)·Φ·Φᵀ·diag(q)` with real feature rows Φ; never assume a
hand-blended distance matrix is PSD.

**Story ordering (after selection):** assign primary roles (ESTABLISHING, ARRIVAL,
PEOPLE_INTRODUCTION, PORTRAIT, GROUP, ACTIVITY, CANDID_INTERACTION, DETAIL,
TRANSITION, CLIMAX, CLOSING); chronology is the backbone with only small local
reorders (establishing before close-ups, avoid portrait-portrait runs, stronger
closer) — never a materially false sequence; transition costs penalize adjacent
near-dups/same-pose repeats and reward wide→medium→close and arrival→climax→closing
arcs. Layout feedback (needs one landscape hero, two complementary portraits, a
panorama, a detail) can later request replacements from the selector — do not block
the curation work on it.

---

## 16. Performance, thermal, background

WorkManager with charging + idle constraints for Tier C; foreground service (with
Android 15+ timeout handling) for user-initiated "index now" with progress;
`PowerManager.getThermalHeadroom()` between batches, pause on severe; battery
thresholds; storage checks; slice work into resumable batches sized for Android 16 job
quotas. Memory: bounded bitmap/tensor lifetimes, no simultaneous GPU spikes unless
benchmarked. Respect Android 14+ Selected Photos Access (partial grants) and
permission revocation.

---

## 17. Reliability and recovery

Process death: leases expire, jobs resume, no corrupt state. Model update: invalidate
per the dependency graph only. Photo edit: new asset revision invalidates that photo's
signals only. Permission revocation: mark assets inaccessible; purge on permanent
revocation or user reset. DB corruption: detect, rebuild from sources, report. Low
storage: pause Tier C, degrade gracefully. Analysis failures: bounded retries with
backoff, quarantine poison inputs, log. Configuration is versioned; every result row
knows which config produced it.

---

## 18. Evaluation program and standing gates

**Structural (free) supervision:** same-photo faces = different-person negatives;
bursts = natural ranking tasks; exact duplicates validate fingerprinting; favourites
and replacements hint preference. Useful for bootstrapping, never perfect ground truth.

**Annotation tool** (local, developer-only), four modes: face pair (same / different /
unsure); burst winner (pick best of 2–6, optionally rank top two); photo pair (better
social post / better family-album photo / better technical capture); album A/B
(two 20–30-photo albums from one event: better representation, less repetitive, would
share, would print). **Active learning:** annotate near thresholds, close relatives,
infant gaps, proposed merges, conflicting-signal bursts, selector disagreements,
repeatedly replaced photos — not obvious pairs. Dataset targets: 500–1,000 same-person
pairs; 2,000+ ordinary negatives; every high-similarity same-photo negative; hundreds
of close-relative negatives; infant positives across adjacent and long gaps; manual
labels for the 20–50 most important people; 500 burst groups; 1,000–3,000 photo pairs;
50–100 held-out events; 100+ album A/B over time. Split train/val/test **by event**.
One owner's labels optimize the owner's experience — broad "social-media standard"
claims need external evaluators later. Freeze held-out sets before tuning; repeated
peeking turns a test set into a dev set. When labels are sparse, use **disagreement
audits** (old vs new system, fp32 vs int8, centroid vs multi-prototype, heuristic vs
submodular) and review only changed outcomes.

**Standing gates — run on every PR touching a model, runtime, threshold or scorer:**

1. *Frozen-pair drift.* Pair categories: adult same-person short/long gap; infant
   same-person adjacent / 6–12 mo / 12–24 mo; same-photo close-relative negatives;
   different-photo close-relative negatives; ordinary negatives; low-quality/profile
   positives. Report ROC, TAR@FAR 1%/0.1% (0.01% where samples permit), EER,
   similarity histograms, false accepts/rejects at current thresholds, cluster-level
   false merge/split changes, and the count of pairs crossing 0.449/0.600 in either
   direction.
2. *Degradation monotonicity.* 200 photos × {blur σ = 1/2/4, ±1.5 EV, crop through
   the main face} — every score must decrease monotonically; violations <2%.
3. *Eyes-open ordering.* In burst groups where ML Kit eye-open differs (≥0.8 vs ≤0.2,
   UNKNOWN excluded), scorers rank the open frame higher ≥95%.
4. *Semantic fidelity* set (section 8) for any TinyCLIP variant.
5. *Fixture-album diff* for the pinned events, printed for human review.
6. *Timing report* against section 3 baselines and section 22 targets, cold/warm
   separated.

Metric vocabulary: identity (B-cubed P/R/F1, pairwise P/R, false merges > false
splits, key-person fragmentation, unassigned rate, same-photo violations); duplicates
(precision/recall per kind, representative top-1 accuracy, distinct-view false
collapse); ranking (pairwise accuracy, NDCG within burst, Spearman/Kendall, burst
winner top-1, closed-eye miss rate, severe-blur selection rate); albums (A/B win rate,
replacements per 20, accept-without-change rate, near-duplicate rate,
moment/day/location/person coverage, dominance, shot-type diversity, pose repetition,
severe-failure rate). **North star: how many edits before the user happily saves,
shares or prints the album.**

---

## 19. Milestone roadmap

One milestone per Claude Code run. Sequencing principle: storage correctness →
persistent computation → runtime acceleration → identity quality → candidate quality →
set selection → learned preference → advanced framing/layout.

| M | Outcome | Primary risk removed |
|---|---|---|
| M0 | Repo audit, model manifest, fixtures, standing gates | Unknown code paths, unmeasurable regressions |
| M1 | SQLite binary migration, dual-read parity | 6.7 s Hermes parse, all-or-nothing loads |
| M2 | Tier A/B/C progressive analysis, job queue | Repeated 148 s cold work |
| M3 | Inference benchmark ladder + acceleration | 140 s / 64-photo TinyCLIP bottleneck |
| M4 | Multi-prototype identities (shadow → active) | Fragmented relatives, infant drift |
| M5 | Duplicate/burst/moment hierarchy + candidates | Top-64 coverage loss |
| M6 | Constrained submodular selector | Discrete-key diversity heuristic |
| M7 | Learned multi-output quality scorer | Rules can't rank beauty/social appeal |
| M8 | Multi-person framing + crop readiness | Single-body framing limit |
| M9 | Story, personalization, layout feedback | Album polish and taste |
| Gate | Licensing, privacy, release validation | Commercial/legal risk (separate) |

Milestone acceptance (condensed; every milestone also passes the standing gates and
ships a completion report):

- **M0:** actual code paths documented with files/functions; every bundled model has
  SHA-256, active/unused status, shapes, dtypes, preprocessing record; current timings
  reproduced within run variance; ≥3 event fixtures with expected selected-photo IDs;
  current cluster counts and key-person fragmentation recorded; standing-gate suites
  runnable by one script; storage-library decision recorded with repo evidence; **no
  behaviour change**.
- **M1:** versioned schema migration; idempotent, resumable import; counts match
  legacy; IDs and cluster assignments unchanged; embedding round-trip parity; fixture
  albums unchanged; startup no longer parses JSONL; bounded batch queries; no
  multi-MB JS embedding representation; before/after timings reported; legacy files
  retained.
- **M2:** signal rows carry asset/model/preprocessing versions; user-event jobs
  outrank backfill; signals reused across filters; process death resumes; cancellation
  commits completed items and releases leases; asset revision invalidates only
  dependents; cache hit rate and avoided inference logged; repeat build no longer
  depends on an in-memory cache alone.
- **M3:** benchmark covers cold/warm init + first/steady inference on identical
  inputs; fidelity metrics reported; operator coverage reported; thermal behaviour for
  a 64-photo sequence reported; runtime fallback tested; **no face INT8 production
  switch without the labelled verification benchmark**; backend switch stays
  feature-flagged until gates pass.
- **M4:** 1–6 prototypes selected deterministically with time/view coverage;
  same-photo high-similarity pairs remain cannot-linked (no escape) unless an explicit
  evidenced exception exists; merges require multiple evidence pairs; user corrections
  persist; merge/split audit queryable; shadow metrics report false merges/splits; key
  infant/relative fragmentation improves or the blocker is precisely documented.
- **M5:** candidate pool built after duplicate/burst grouping; every important moment
  contributes; budget configurable; reservations visible in debug explanations; old vs
  new pools comparable; unique-moment recall improves on fixtures; deep analysis stays
  bounded by budget.
- **M6:** objective components unit-tested; deterministic lazy greedy; hard
  constraints never violated; explicit soft-relaxation order; bounded deterministic
  swap pass; per-photo marginal-gain explanation; A/B report with quality, coverage
  and repetition; current planner retained for rollback.
- **M7:** train/held-out events separated; checkpoint supplied or produced by the
  pipeline; pairwise ranking beats the hand-crafted baseline; group lower-tail
  behaviour evaluated; severe-defect selection non-inferior; album preference
  measured; uncertainty stored and used.
- **M8:** multi-person models evaluated on real group photos; pose recall vs face
  count reported; segmentation download/unavailable states handled; framing emits the
  three states; crops preserve important faces; latency/memory fit a finalist-only
  budget; fallback works; **no completeness guarantee claimed**.
- **M9:** ordering stays materially chronological; feedback updates the bounded local
  preference model; personalization can be disabled/reset; layout roles can request
  replacements; held-out edit/reorder count improves.

**Deferred (off the critical path):** post-M3, if M7 heads prove ceiling-limited by
TinyCLIP-8M/16 features *and* compute headroom exists, run a backbone-replacement
experiment (e.g., MobileCLIP2-S0 image tower) through the same fidelity and standing
gates — invalidation and concept-constant regeneration are already handled by the
versioning system; its licensing belongs to the release gate. Live/Motion Photo best
frames, joint selection-layout optimization, and any sensitive-content classifier are
likewise later.

---

## 20. CURRENT PHASE — M0 audit + M1 SQLite binary migration

**Objective.** Establish measurement, then remove the storage bottleneck — with zero
algorithm changes.

**Before coding**, report: relevant files/functions and current data flow; the actual
JS/native boundary; how `face-index.json` and `face-observations.jsonl` are read and
written; which of the two face models is active; existing tests; the SQLite library
choice against section 5 requirements with repo evidence; the exact change strategy.

**Required work.**

1. M0 deliverables: architecture map; model manifest (SHA-256, shapes, dtypes,
   preprocessing, active/unused); structured timing events; ≥3 private event fixtures
   with expected selected-photo IDs; baseline identity/duplicate metrics where labels
   exist; the standing-gate suites (frozen pairs, degradation, eyes-open) runnable via
   one script against an exported index; a list of unverified assumptions.
2. M1 deliverables: schema (section 5 sketch, adapted) with versioned migration;
   BLOB embedding codec with sampled round-trip parity; incremental, idempotent,
   resumable importer with source fingerprinting and dual-read validation; bounded
   query APIs; startup path that never parses the full JSONL; feature-flagged switch
   with legacy files retained.

**Non-negotiable parity:** photo/person/face counts match; existing IDs and cluster
assignments unchanged; fixture album outputs identical; no model, threshold or
preprocessing touched.

**Tests:** migration idempotence and resume-after-kill; codec parity; bounded-query
correctness vs legacy reads; fixture-album regression; timing before/after
(embedding-load target <150 ms vs 6.7 s baseline).

**Completion report:** measured startup and query timings, parity results,
gate outputs, storage-library rationale, blockers.

---

## 21. Configuration seeds (versioned, tunable — never hardcoded constants)

```text
identity.assignment_bar        0.449   (existing; recalibrate on any model change)
identity.merge_bar             0.600   (existing; recalibrate on any model change)
identity.pair_support_min      2 pairs from ≥2 source photos
identity.chain.window_days     45
identity.chain.pair_bar        0.52
identity.prototypes.max        6 (size-tiered: 1 / 2 / 4 / 6)
dupes.fingerprint_bar          0.92    (until labelled benchmark)
candidates.budget              clamp(5K, 96, 192), cap 256
selector.sim_blend             v1 weights per section 15
selector.coverage_curve        h(n) = 1 − e^(−τn), τ per category (v1 table 0/1.0/1.45/1.65)
quality.group                  0.55·wMean + 0.30·wP20 + 0.15·importantMin (hypothesis)
```

## 22. Numeric targets (initial gates to validate — a miss triggers the next ladder rung or a documented blocker, never silent relaxation)

```text
embedding load path            < 150 ms          (baseline 6.7 s)        after M1
warm album build, 3k filter    ≤ 5 s; repeat ≤ 2 s (baseline 148 s / 26 s) after M2+M5
deep signal per photo          ≤ 1.4 s after FP16-CPU; ≤ 0.8 s after INT8  in M3
face pair-AUC drop per change  ≤ 0.2 pt after any threshold re-fit         gate 1
degradation violations         < 2 %                                       gate 2
eyes-open ordering             ≥ 95 %                                      gate 3
NN recall@10 (TinyCLIP var.)   ≥ 0.98 on local benchmark                   gate 4
Tier-C full-library backfill   completes within a few charging sessions;
                               if the M3 winner projects worse, that is the
                               trigger for the native-LiteRT / NPU rungs
key-person fragmentation       ≥ 60 % reduction vs 2,237-cluster baseline,
                               zero auto-confirmed impostor merges          M4
```
