# Desktop vs Android — image selection flow, gap analysis

Goal: the Android app should run the **same selection algorithm** as the desktop
engine, choosing phone-appropriate models but keeping the algorithm identical.
This maps the desktop flow stage-by-stage to what Android has today (after
selection-quality v2 + faces + location landed), and lists exactly what's missing.

## The core difference (read this first)
The desktop engine's thesis is literally *"THE PROBLEM IS NOT QUALITY. IT IS
COVERAGE."* (`album-engine/selection.py`). Desktop selection is a **greedy
marginal-gain coverage optimizer**: it picks N from thousands by maximizing
coverage across **time · place · moment · people · body-pose** on top of quality,
with a **hard people floor** (never omit a person), MMR redundancy, per-axis caps,
and rich per-face gates.

The Android app today (`select-best-shots.ts` v2) is only the **quality/dedupe
front-half**: collapse near-duplicates into "takes", pick the best frame per take
(category-weighted sharpness/eyes/smile/cut-face, blink gate), sort takes by
quality, take the top N. It has **no coverage optimization and no rich signals** —
it's a quality ranker, not an album planner.

So two things are missing: (1) the **coverage selection algorithm** (pure logic,
portable, no model), and (2) the **signals that feed it** (need phone models).

## Stage-by-stage map

| Desktop stage (what it does) | Android today | Gap |
|---|---|---|
| **Semantic embedding** (SigLIP) — powers dedup, redundancy, moment & shot grouping | Perceptual 76-dim fingerprint (luma grid + RGB hist) | **Weak.** Not semantic — groups by look, not content. Needs a mobile image embedder. |
| **Near-duplicate dedupe** (shot groups, cosine ≥0.93 in 1h burst window) | Take grouping, cosine ≥0.92 on perceptual embedding | Partial — logic present, embedding weak. |
| **Face detection + landmarks** (SCRFD/YuNet) | ML Kit face detection (boxes, eyes-open, smiling, head Euler angles) | **Have** (ML Kit compiles on-device ✓). |
| **Face identity** (ArcFace embedding → person clustering → `person_ids`) | `face-cluster.ts` greedy cosine on **perceptual face-crop** embedding | Partial — clusters, but not identity-grade. Needs MobileFaceNet. |
| **Per-face expression** (eyes_open, smile) worst-face aggregation + blink gate | ML Kit eyes-open/smiling → worst-face blink gate, smile tiebreak | **Have** (quality v2). |
| **Face-region sharpness** (`face_sharpness_floor`), **head-region sharpness** (`head_sharpness_floor`) | Whole-image Laplacian sharpness only | **Missing** face/head-region measures (we already crop faces — cheap to add). |
| **Face exposure** (backlit-silhouette floor on worst face luma) | Whole-image exposure/clipping only | **Missing** per-face exposure. |
| **Head pose** yaw/pitch/roll → tightens identity confidence >±45° | — (ML Kit returns Euler angles, **unused**) | **Missing** wiring (data is free from ML Kit). |
| **BODY POSE** (RTMO 17 COCO keypoints → joint-angle signature → mirror-invariant clustering, 22°) → **pose-diversity axis + per-pose caps** | — nothing | **Missing entirely.** This is "pose detection". Needs a keypoint model; the clustering algo (`pose.py`) is pure and ports verbatim. |
| **Zero-shot expression axes** (SigLIP text-image contrasts): aesthetic, composed, clean_frame (bystander), sleeping, embrace_context, screenshot/document | Screenshot only, via filename+aspect heuristic | **Missing** aesthetic/composed/clean/sleeping/embrace (need CLIP-style contrasts). |
| **Exposure/clipping gate** (unrepairable-clipping reject) | Whole-image clipping measured | Partial (whole-image, not per-face). |
| **Capture-time coverage axis** (time buckets, undated ≠ day-one) | `creationTime` available, **unused in selection** | **Missing** the axis. |
| **Place coverage axis** (GPS geo-cells) | `photo-index.ts` has city/country, **unused in selection** | **Missing** the axis. |
| **Moment novelty axis** (loose embedding grouping, cosine ≥0.80 in 6h) | — | **Missing.** |
| **People floor** (min 1/person via greedy max-coverage) + per-person cap + non-people fraction | — | **Missing.** The desktop's #1 rule ("never omit a child"). |
| **MMR redundancy** with library-calibrated free-similarity zone | Hard dedupe at 0.92 only | **Missing** the calibrated soft penalty. |
| **Comparability classes + within-class standing** (rank portraits vs landscapes fairly) | Single flat quality score | Missing (only matters once measurements differ per photo). |
| **Waivers**: rare-moment (time-isolated) + scarce-person quality-floor waivers | — | **Missing.** |
| **Category-conditioned weights** (portrait/couple/group/detail) | **Have** (quality v2 has exactly this) | Have. |
| **Hard gates**: screenshot/document, clipping, backlit face, cut-face, min-pixels | Screenshot(heuristic) + cut-face + whole-image clipping | Partial. |
| **User pins / excludes / swaps** (sovereign, re-gen respects choices) | — | **Missing.** |
| **Determinism** (media_id sort, quantized gains, stable tiebreak) | Deterministic already | Have (simpler form). |

## What needs to be built (ranked)

### Layer 1 — Port the coverage selection ALGORITHM (pure logic, no models)
This is the biggest correctness gap and needs **zero new models** — it runs on the
signals we already have (perceptual embedding + ML Kit faces + creationTime +
photo-index place + face clusters). Port `album-engine/selection.py`'s `select()`
to TypeScript faithfully:
- greedy marginal-gain over **time · place · moment · person · pose** + quality
  standing + MMR redundancy;
- **people floor** phase (min per person, max-coverage) + per-person cap +
  non-people fraction;
- per-pose-family and per-body-pose caps;
- rare-moment + scarce-person waivers; pins/excludes.
`pose.py` ports **verbatim** (pure joint-angle math). Even feeding it today's
weak perceptual embedding, this turns "top-N by quality" into a real
coverage-optimized book — the single largest jump toward desktop parity.

### Layer 2 — The phone models that feed it (react-native-fast-tflite; New-Arch works, ML Kit proved it)
1. **Body-pose keypoints → pose axis** (the "pose detection" you asked about):
   **MoveNet SinglePose Lightning** (or Thunder) .tflite → 17 COCO keypoints →
   feed the ported `pose.py` **unchanged**. Enables the pose-diversity axis + caps.
2. **Semantic image embedding** (replaces the perceptual fingerprint everywhere):
   **MobileCLIP-S0/S1** or a small SigLIP → real dedup, redundancy, moment/shot
   grouping — and, via text-image contrasts, the **zero-shot expression axes**
   (aesthetic, composed, clean_frame, sleeping, embrace, screenshot). One model
   unlocks the most gaps.
3. **Face identity embedding**: **MobileFaceNet** (ArcFace-style) .tflite → real
   person clustering (upgrades `face-cluster.ts` from perceptual to identity),
   which makes the **people floor** trustworthy.

### Layer 3 — Cheap wins (pure/portable, no new model)
- Face-region + head-region sharpness, per-face exposure (we already crop faces).
- Head-pose (yaw/pitch/roll) from ML Kit → identity-confidence tightening.
- Wire `creationTime` (time axis) and `photo-index` place (place axis) into selection.

## Honest ceiling
Desktop uses SigLIP-so400m + SCRFD/ArcFace + RTMO — too big for a phone verbatim.
The **algorithm** ports exactly; the **models** become mobile equivalents (MobileCLIP,
MoveNet, MobileFaceNet). Expect the same *decisions* with slightly noisier signals.
The one thing that can't be matched cheaply is SigLIP-grade semantic nuance; MobileCLIP
is the closest phone-viable substitute.

## Suggested sequencing
1. **Layer 1** first (pure algorithm port + pose.py) — biggest parity jump, no model risk.
2. **MoveNet** (pose axis) — self-contained, high visible value, algo already ported.
3. **MobileCLIP** (semantic embedding + zero-shot axes) — unlocks the most remaining gaps.
4. **MobileFaceNet** (identity) — makes the people floor real.
5. Layer 3 cheap wins fold in alongside.
