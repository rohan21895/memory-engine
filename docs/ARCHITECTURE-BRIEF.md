# Photeo — architecture and open problems

An Android app that scans a phone's photo library, groups people by face, and builds an album of
the best photos for a chosen event or trip — entirely on device, nothing uploaded.

Every number below was measured on the owner's own phone and library, not estimated.

---

## The library it has to handle

| | |
|---|---|
| Photos indexed | 11,853 |
| On device | 40 GB |
| Mean photo size | 4.7 MB |
| Faces detected | 17,766 |
| People clusters | 2,237 |
| Clusters with 2+ faces | 932 |

A family library: mostly group photos, a small cast of relatives, spanning an infant's first two
years. Relatives resemble each other, which matters for every threshold decision below.

---

## Pipeline as built

| Stage | How it works | Model / method |
|---|---|---|
| Detect faces | Per photo during a background scan | ML Kit — box, landmarks, eye-open probability, head Euler angles |
| Embed identity | Aligned crop → 512-d vector | `w600k-mbf` (InsightFace buffalo), 13.6 MB **float32** |
| Group people | Average-linkage agglomerative, **one centroid per person** | Assignment bar 0.449, merge bar 0.600 |
| Same-photo rule | Two faces in one frame are cannot-linked, unless similarity ≥ 0.72 (mirror / photo-of-photo escape) | Hard constraint, inherited through merges |
| Body pose | 17 keypoints → joint-angle signature → pose clusters | MoveNet Lightning, 2.9 MB int8 |
| Semantics | Image embedding for content similarity and screenshot rejection | TinyCLIP ViT-8M/16, 33.2 MB **float32** |
| Near-duplicates | 76-value perceptual fingerprint, collapse above 0.92 | Bursts, reframes, orientation changes |
| Quality | Regional sharpness on the subject, eyes-open, cut-face rejection | Hand-crafted rules |
| Select | Prepass ranks all photos → top 64 get deep analysis → planner picks ~24 using diversity keys (time, place, content, person, pose) | Hand-tuned scoring |

**Storage:** two flat files — `face-index.json` (2.5 MB, people and metadata) and
`face-observations.jsonl` (13.8 MB, quantized embeddings). No database.

**Inference:** TFLite via `react-native-fast-tflite`, XNNPACK **CPU only**.

**Bundled models:** ~55 MB total, only MoveNet quantized.

```
mobilefacenet-192-float32.tflite            5.2 MB
movenet-singlepose-lightning-int8.tflite    2.9 MB
tinyclip-vit-8m16-image-float32.tflite     33.2 MB
w600k-mbf-512-float32.tflite               13.6 MB
```

---

## Problems, most consequential first

### 1. Float32 models on CPU, with the GPU path closed — *speed, blocked*

A 33 MB float32 ViT runs per candidate photo on CPU. Deep analysis of 64 photos costs about
140 s of a 148 s album build — roughly **2.2 s per photo**.

The GPU delegate is deliberately disabled in code, not by oversight: `fast-tflite 3.0.1`
hardcodes GPU delegate options with no serialization directory, so kernels recompile on every
cold start, and pins `max_delegated_partitions=1`. NNAPI is deprecated on Android 15.

```
album build, 3,000 photos selected
  cache-load       758 ms / 3000
  candidate-probe 3263 ms / 3000
  candidate-rank  2368 ms / 3000
  deep-analysis    ~140 s / 64      <- the whole cost
```

**Question for an expert:** int8 or fp16 quantization of TinyCLIP and w600k, versus fixing the
delegate path (patched fast-tflite, LiteRT directly, or a vendor delegate such as QNN/Hexagon on
Snapdragon). Which gives more, and what accuracy loss should be expected on face embeddings
specifically?

### 2. One centroid per person cannot represent a growing child — *accuracy, structural*

2,237 clusters for a family library. The same people occupy several tiles each. This is **not** a
threshold that needs tuning — that was measured and ruled out.

Only 8 pairs in the entire library clear the 0.600 merge bar, and all 8 are already blocked by
the same-photo rule. The genuine splits sit at **0.50–0.52** average linkage.

Using photos-taken-together as known-different-people labels:

| Merge bar | Different people admitted | Real merges gained |
|---|---:|---:|
| 0.60 (current) | 8 | 0 |
| 0.50 | 40 | 8 |
| 0.45 | 59 | 106 |
| 0.40 | 80 | 326 |

Every bar low enough to join the splits admits more impostors than it gains merges — and that
counts only people who co-occur, so the true error is larger. A single mean vector sits between a
3-month-old and a 14-month-old and matches neither.

**Direction to validate:** multiple prototypes per identity (sub-center ArcFace style),
quality-weighted centroid contributions, and time-aware linking for infant appearance drift.

### 3. Analysis happens at album time, not scan time — *architecture*

Pose, semantics, sharpness and quality are computed when an album is built, then discarded.
Choosing a different filter re-runs everything. The scan already visits every photo once and has
the decoded pixels in hand.

```
3,000-photo album:  148 s cold  ·  26 s repeat (probe cache)
selected: 24 photos
```

### 4. 13.8 MB of embeddings parsed as JSONL — *storage*

```
observations bytes=13827052  readMs=137  parseMs=6694
```

Disk is not the problem — the read is 137 ms. `JSON.parse` in Hermes is **6.7 s**, and it is
atomic, so it cannot yield. Anything needing embeddings stalls for that long. There is no
database; queries are all-or-nothing loads.

### 5. "Best photo" is hand-crafted rules, not a learned score — *quality*

Sharpness, eyes-open and cut-face detection decide quality. There is no aesthetic model. Rules
are good at ruling out the unacceptable and poor at ranking the beautiful.

A measured caution: every **hard** quality gate tested cost real selections — between 2.7% and
13.8% of currently chosen photos — on a library that is mostly group shots where background faces
are legitimately soft from depth of field. (That range came from a synthetic corpus; treat it as
the shape of the trade, not a fact about this library.)

### 6. Diversity is approximated by discrete keys — *selection*

The planner dedupes on string keys — `pose:N`, time bucket, place, person set. The goal ("the
most unique and memorable collection") is a quality-and-diversity subset selection problem,
currently solved by hand-tuned heuristics rather than an objective.

**Direction to validate:** greedy MAP inference over a determinantal point process, kernel
`L = diag(q)·S·diag(q)`, with `S` a weighted blend of CLIP distance, face-set overlap, pose
distance, GPS and time. At n=64 the cost is negligible.

### 7. Body coverage is single-person only — *known limit*

MoveNet Lightning fits one person. Framing signals — head / torso / full body, and whether the
subject is cut by the frame — describe whichever body the model locked onto and say nothing about
the others in a group photo.

There is no segmentation model available: ML Kit ships face detection only here, with no pose or
selfie segmentation, so "no cut hair or limbs" cannot currently be guaranteed.

---

## Proposed order of work

1. **Quantize the models and settle the delegate question.** The largest single cost, and pure
   engineering with no product risk.
2. **Move the index into SQLite.** Removes a 6.7 s stall and makes incremental queries possible
   instead of whole-file loads.
3. **Compute per-photo signals once during the scan and store them.** Turns album building from
   minutes into a query plus a selection pass.
4. **Add a learned aesthetic head.** A small MLP on the CLIP embedding already being computed —
   near-zero marginal inference cost.
5. **Replace the diversity keys with a DPP.** One objective covering pose, people, scenery, place
   and time together.
6. **Multi-prototype identities.** The real fix for over-fragmentation, and the one most worth a
   second opinion before building.

---

## The questions worth an expert's time

- Quantization versus delegate work — which recovers more on mid-range Android, and what does
  int8 cost a 512-d face embedding in verification accuracy?
- Is multi-prototype identity the right answer to infant appearance drift, or is an age-aware /
  quality-weighted embedding the better lever?
- Is a DPP the right selection objective here, or does submodular coverage maximization fit a
  photo album better in practice?
- Is there a subject-segmentation model small enough to ship on-device that would make "no cut
  limbs or hair" a guarantee rather than a heuristic?
- What is a defensible way to evaluate "best photo" quality without a labelled set — beyond the
  owner's own judgement?
