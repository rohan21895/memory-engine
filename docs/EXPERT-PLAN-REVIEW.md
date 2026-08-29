# Review of the expert plan

> **Historical, pre-measurement review. Do not use as live guidance.** Later experiments
> rejected this document's endorsements of multi-prototype identity and facility
> location, corrected the deep-analysis timing attribution, and respecified M1. The
> authoritative plan is [`EXPERT-PLAN.md`](EXPERT-PLAN.md); the short status is
> [`EXPERT-PLAN-STATUS.md`](EXPERT-PLAN-STATUS.md).

What we accept, what is already built, where the plan is out of date, and what we
think it gets wrong about ordering. The plan itself is `docs/EXPERT-PLAN.md`;
Codex's line-by-line verification is `docs/CX-21-PLAN-AUDIT.md`.

The plan was written from `docs/ARCHITECTURE-BRIEF.md`, which is a summary. Several
of its factual premises describe the app as the brief described it rather than as the
code is, and two of them are load-bearing for its milestone order.

---

## Verdict

**The plan is good and we should follow most of it.** The engineering judgement is
sound, several of its recommendations are better than what we had proposed, and three
of them independently confirm things we measured ourselves — which is the strongest
signal available that they are right.

We disagree with the milestone **order**, not the milestones.

---

## Adopt without argument

**The framing.** "Photo scoring decides whether a photograph is good; album
optimization decides whether it adds value to this collection." We have been
conflating the two — `select-best-shots.ts` mixes quality and diversity in one pass.
Separating them is the right shape.

**Submodular over DPP.** We asked the expert whether a DPP was the right selection
objective. The answer — facility-location + saturating coverage with lazy greedy —
is better than what we proposed, and for a reason we had not considered: a DPP kernel
must be PSD, and the hand-blended similarity matrix we were going to feed it (CLIP
distance + face overlap + pose + GPS + time) is not. Lazy greedy also takes hard
constraints natively, which DPP MAP does not. We drop the DPP to an offline baseline.

**Never train on like counts.** Correct, and it saves a wrong turn we would have taken.
Likes measure audience and posting time, not the photograph.

**Structural supervision.** Same-photo faces as free different-person labels is
something we already do. Bursts as free ranking labels, and exact duplicates as
fingerprint validation, are free and we were not using them.

**Framing as a soft penalty, never a hard gate.** This matches CX-19's measurement on
this codebase exactly — every hard framing gate tested cost real selections. Arrived
at independently, which is worth more than either finding alone.

**Multi-prototype identities (medoids, not means).** The genuinely new idea, and the
right answer to the fragmentation we could not fix with thresholds. Real, unbuilt, and
the single highest-value item in the plan.

**Standing gates.** Frozen-pair drift, degradation monotonicity, eyes-open ordering.
Cheap, and they would have caught at least two regressions we shipped.

---

## Already built — do not rebuild

### M1's headline premise is stale: startup does not parse the JSONL

The plan's M1 acceptance criterion is "startup no longer parses JSONL". It already
doesn't. `INDEX_VERSION 22` split embeddings out of the index file into
`face-observations.jsonl` for exactly this reason — the comment at
[face-index.ts:45](apps/mobile/src/faces/face-index.ts:45) records the measurement
that motivated it (`readMs=84 parseMs=5993`, "six seconds of a frozen JS thread on
every launch").

It is also no longer atomic. `OBSERVATION_LOAD_CHUNK = 500`
([face-index.ts:70](apps/mobile/src/faces/face-index.ts:70)) parses a chunk, yields to
the UI, and continues — one JSON object per line specifically so it *can* be
interrupted.

So the 6.7 s is paid **on demand**, by things that need embeddings (merge review,
recluster), and it yields while it runs. That is a much smaller problem than the plan
believes. SQLite is still worth doing — for incremental queries and for not holding
17.8k embeddings in JS heap — but its headline benefit is already delivered and its
priority drops accordingly.

### Candidate selection is not a "global top-64 by quality"

`chooseHeavyAnalysisCandidates`
([candidate-prepass.ts:117](apps/mobile/src/selection/candidate-prepass.ts:117))
already rewards underrepresented time windows, places, coarse visual content, and
recurring people. CX-16 added those axes for precisely the reason the plan gives.

The plan's *diagnosis* still stands — the cap is applied before moments exist, and
there are no explicit reservations — but the fix is not "add diversity awareness". It
is to raise 64, and 64 is not a tuning choice: the comment at
[candidate-prepass.ts:11](apps/mobile/src/selection/candidate-prepass.ts:11) records
that the TFLite runtimes serialize their queues and 64 is the largest pool that fits
the time budget. **The candidate budget is a function of inference cost.**

### Temporal chaining and evidence-gated merges exist, and are calibrated rather than guessed

The plan proposes temporal chaining as new M4 work with seed constants: 45-day window,
chain bar 0.52, ≥2 supporting pairs. We have all three concepts already:

| Plan | In the code |
|---|---|
| chain window 45 days | `TEMPORAL_MERGE_WINDOW_MS` = 60 days ([face-cluster.ts:75](apps/mobile/src/faces/face-cluster.ts:75)) |
| chain pair bar 0.52 | `temporalMergeBar()` — evidenced bar minus one sigma, **derived from this library's own impostor distribution** ([face-index.ts:1927](apps/mobile/src/faces/face-index.ts:1927)) |
| ≥2 supporting pairs | `MERGE_EVIDENCE_MIN_FACES` = 4 on both sides ([face-cluster.ts:46](apps/mobile/src/faces/face-cluster.ts:46)) |

Ours are measured; the plan's are hand-picked seeds. Adopting section 21 literally
would replace calibrated constants with guesses. We take the plan's *structure* for
M4 (multi-prototype) and keep our own bar derivation.

The plan also does not know about `centeredForClustering`
([face-index.ts:1882](apps/mobile/src/faces/face-index.ts:1882)) — embeddings are
mean-centred before clustering. Any threshold the plan quotes lives in that centred
space, not raw ArcFace space.

### Long-lived model instances — done, and the plan's reason is the wrong one

"Keep long-lived model instances (never load TinyCLIP 64 times)" is done
([model-cache.ts](apps/mobile/src/ml/model-cache.ts)). But the constraint is the
opposite of what the plan assumes: fast-tflite v3 never returns the interpreter arena
between runs (mrousavy/react-native-fast-tflite#124), so native memory climbs from
~200 MB to ~1.2 GB across a long batch and OOM-kills the app. We are *forced* to retire
each interpreter every 400 inferences, and `dispose()` on a nitro HybridObject is an
empty body — there is no deterministic release at all
([model-cache.ts:86](apps/mobile/src/ml/model-cache.ts:86)).

This strengthens the plan's "thin native LiteRT module" recommendation, for a memory
reason it did not have. It is the best argument in favour of that work.

---

## Where the plan is wrong for this codebase

### "TinyCLIP fp32 on CPU is the dominant cost" is an assumption, not a measurement

This is the load-bearing claim under all of M3, and nobody has measured it.

Deep analysis runs **five** things per photo, concurrently but serialized in the native
queue ([build-album.ts:578](apps/mobile/src/build-album.ts:578)): the 76-value
perceptual model, ML Kit face detection, a full pixel decode for quality, MoveNet, and
TinyCLIP. The only timer is around the whole loop
([build-album.ts:631](apps/mobile/src/build-album.ts:631)). There is no per-model
breakdown.

If decode is 1.2 s of the 2.2 s, quantizing TinyCLIP buys a fraction of what the plan
projects. On this codebase, when we have guessed at where time goes, we have been
wrong every time — a single `O(n²)` log line once cost 2.8 s at startup.

**Instrumenting the five stages is the first task of M3 and it gates the rest of it.**

### The sequencing is wrong: M3 should come first

The plan runs M1 (SQLite) → M2 (progressive analysis) → M3 (inference). We think M3
has to move up, because two later milestones are gated on it and M1's benefit is
already banked:

- **M2 depends on M3.** Tier A is per-photo analysis over 11,853 photos. The plan
  itself computes that at current speed this is 7.2 h and calls it unacceptable —
  then schedules it before the milestone that fixes the speed.
- **M5 depends on M3.** The target budget is `clamp(5K, 96, 192)`. At 2.2 s/photo,
  192 candidates is seven minutes of deep analysis. The budget is unreachable until
  inference is cheaper; the cap of 64 exists for that reason today.
- **M1's stated benefit is already delivered** (above), so it is not buying the
  unblocking the order implies.

Our order: **M0 → M3 → M2 → M4 → M5/M6 → M1 → M7/M8.** M0 is unchanged and cheap.
SQLite moves to where it is actually needed — once progressive analysis is writing a
per-photo signal row per model version, flat files stop being adequate. That is a
consequence of M2, not a prerequisite for it.

### The plan under-weights the thing currently doing the most good

It treats user corrections as an input constraint. On this library they are the
product surface that does the repairing — we measured that **no merge threshold fixes
the splits**: every bar low enough to join genuinely-split people admits more
impostors than it gains merges. Multi-prototype identity may change that; until it
does and is proven in shadow mode, the ranked merge review is the only safe repair,
and improving it (co-occurrence evidence on the card, better ranking, fewer questions
for the same repair) beats anything in M1–M9 on a weeks horizon.

---

## Measured: the 0.72 escape is dead, and what it is hiding

The plan demands the `SAME_PHOTO_EXCEPTION_SIMILARITY = 0.72` escape be removed. We
measured it on the owner's real index (2,173 people, 17,699 faces) before deciding.

```
co-occurring cluster pairs                  7986
  of those, >= 0.72 (escape fires)             0
highest co-occurring pair                 0.6992
```

**It has never fired.** Removing it is free on this library and closes a latent hazard,
so we take the plan's recommendation — but note that it cannot help fragmentation,
because it is not currently letting anything through *or* holding anything back.

The sweep also found low-rate and high-rate co-occurring pairs, but it did not label
them. The earlier text promoted that unlabelled shape into a claim about which pairs
were genuine splits. The current answer audit below shows why that claim must be
withdrawn: only one answer constrains a co-occurring pair.

The rate still belongs on the review card because "together in 2 of 410 photos" is more
useful context than "2 shared photos." It is a ranking and explanation input only, not
evidence strong enough to answer the merge question.

## The finding that reorders everything: nothing is unblocked at the top

We then swept every pair of substantial clusters (both ≥ 4 faces, 453 of them) and
split the results by whether the same-photo rule blocks them:

```
   bar    unblocked   faces they'd repair   blocked by same-photo
  0.60           0                     0                       7
  0.55           0                     0                      15
  0.52           0                     0                      28
  0.50           2                    42                      30
```

**Above 0.52 linkage there are no unblocked pairs at all.** Every substantial cluster
pair the app could merge on similarity, it already has. Everything left at the top of
the distribution is held apart by the same-photo cannot-link — not by the threshold.

The earlier version of this section treated a gap in the unlabelled rate distribution as
if it separated confirmed same-person splits from confirmed different people. Withdraw
that inference: the answer store behind it held 38 records, but 37 were not usable as
identity-level labels.

Re-measured on 2026-08-29 after face-anchored answers shipped, the store holds **89
answers** (85 same, 4 different). Of those, 73 resolve against the current identities:
72 are already satisfied inside one cluster, while only **one answer constrains a current
co-occurring pair**. That pair is labelled different people at 2.7% co-occurrence. No
answered pair is in the narrower over-bar population held apart only by co-occurrence.

One labelled block cannot establish a rate boundary, validate the old bands, or justify
any clustering decision. Co-occurrence remains an absolute constraint and a review-queue
ranking input; only the user-confirmed answer authorises a repair.

The user-confirmed review queue remains the accepted repair path. This measurement does
not establish that low co-occurrence is evidence for either answer; it establishes only
that the labelled set is still too small to decide.

## What Codex's audit added

Full verification in `docs/CX-21-PLAN-AUDIT.md`. The findings that change decisions:

**`w600k-mbf` is the live identity model, definitively** — `createFaceEmbedding` →
`embedFaceIdentity` → the only identity load site. `mobilefacenet-192-float32.tflite` is
**5.23 MB of dead weight**, and `ml/README.md` plus `MODEL-NOTICES.md` still describe the
old 192-d contract. The manifest (SHA-256, shapes, dtypes, exact preprocessing for all
four graphs, 55,011,716 bytes total) is done — most of task #27.

**Decode-once is already built.** The plan's section 7 asks for one bounded working
bitmap shared by every model. One 1280 px proxy already feeds all five deep-analysis
tasks. Another milestone we would have partly rebuilt.

**We already have an aesthetic signal, and the brief was wrong to say otherwise.**
`semanticSignals` scores the TinyCLIP embedding against bundled text axes — `aesthetic`,
`composed`, `cleanFrame`, `screenshotDocument` — weighted 0.55 / 0.25 / 0.35 in the
planner ([tinyclip.ts:312](apps/mobile/src/ml/tinyclip.ts:312),
[album-planner.ts:99](apps/mobile/src/selection/album-planner.ts:99)). So M7's baseline to
beat is zero-shot CLIP, not hand-crafted rules. Higher bar, different experiment. The
brief has been corrected.

**M6 is smaller than the plan implies — L, not XL.** `album-planner.ts` already does
greedy saturating coverage, moment grouping, per-person minimum and cap, rare-moment and
scarce-person rescue, and deterministic reasons. M6 is formalization plus facility
location, lazy bounds and a swap pass.

**M2 is genuinely new.** `candidate-probe-cache.ts` persists only cheap probes — BlurHash
and derived sharpness, exposure, clipped fraction, capped at 20,000 records. No pose, no
TinyCLIP embedding, no ML Kit result, no fingerprint, no pixel quality survives a build.
It also has a latent bug: the cache key covers asset identity but **not** model or
preprocessing version, so changing a probe algorithm silently reuses stale values unless
`CACHE_VERSION` is bumped by hand.

**Segmentation is not an available option.** No dependency or module supplies subject or
selfie segmentation, and MoveNet is single-person. Both MediaPipe Pose Landmarker and ML
Kit subject segmentation need new native dependencies. M8 is bigger than the plan assumes,
and "no cut limbs or hair" cannot be promised today.

**Storage: `expo-sqlite`.** This is an Expo managed/CNG prebuild project — there is no
checked-in `android/`. `op-sqlite` and `nitro-sqlite` are unverified against SDK 57 /
RN 0.86 and are not locked; `react-native-nitro-modules` being present for fast-tflite is
not evidence the SQLite driver works. `expo-sqlite` exposes BLOBs as `Uint8Array`, but
zero-copy is unproven and needs a 17.7k × 512 benchmark before committing.

**Codex reached the same sequencing verdict independently** — it flagged M2-before-M3 as an
ordering defect, and separately found that M5 needs M2 and M3 rather than M4.

**Several section-22 targets are invalid as written**, not merely ambitious:
- *Embedding load < 150 ms* is misbaselined — startup already avoids observations, and
  loading all 17.7k × 512 into JS would violate the plan's own no-materialization rule.
  Define a bounded query instead.
- *Key-person fragmentation ≥ 60% reduction vs the 2,237-cluster baseline* is
  dimensionally invalid: 2,237 is the total cluster count, not a fragmentation metric, and
  no frozen key-person truth set exists. Name the people and define B-cubed first.
- *Eyes-open ≥ 95%* is not currently measurable — ML Kit values are often absent and no
  fixture carries real multi-face pixels.
- *Warm 3k build ≤ 5 s* cannot be met by M5 alone; it needs every required signal already
  persisted by M2.

## Still open, to answer with measurement

1. **Which face model is live?** `apps/mobile/src/ml/README.md` says MobileFaceNet;
   the brief says `w600k-mbf`. Both are bundled. One of them is 5.2 MB of dead APK.
2. **What is the real per-model split of the 2.2 s?** See above — this gates M3.
3. **Stored embeddings are already int8**, base64-encoded, decoded to `number[]` in JS
   ([face-index.ts:828](apps/mobile/src/faces/face-index.ts:828)). The plan's rule
   "embeddings never materialize as JS number arrays or base64" is a fair criticism of
   what we do today — but note its quantization ladder concerns the *model*, and the
   *vectors* have been int8 for some time.
