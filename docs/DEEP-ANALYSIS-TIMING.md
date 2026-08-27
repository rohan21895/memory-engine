# Where the 2.3 seconds per photo actually goes

Measured on the owner's phone (OPlus CPH2649, 11,854-photo library), 2026-08-27, on a
real 3,000-photo album build. Not a benchmark, not a synthetic corpus — the shipping
path, driven through the app's own UI.

This settles the question the expert plan rested on and this repo could not answer.

---

## The answer: TinyCLIP fp32, and it is not close

```
deep-analysis         148,837 ms / 64 photos     (2.33 s per photo)
  tinyclip.model-inference   145,952 ms   mean 2,280 ms   p95 2,604 ms
  movenet.model-inference     71,113 ms   mean 1,111 ms   p95 1,964 ms
```

**TinyCLIP's own inference accounts for 145,952 ms of a 148,837 ms stage — 98%.** It is
the critical path almost exactly. The expert's assumption was right; it is now measured
rather than assumed, which is what M3 was blocked on.

MoveNet is the surprise. It is 2.9 MB, already int8, and still costs **1.11 s of pure
inference per photo**. A quantized 2.9 MB model taking half the time of a 33 MB float32
ViT says the constraint is the CPU itself, not model size, and that argues for the
delegate/runtime work rather than quantization alone.

> **Correction.** The first version of this line called MoveNet "48% of the same wall".
> That is the ratio of the two inference times, but it is not a share of the wall: the
> two models run inside one `Promise.all` (`build-album.ts:690`), so MoveNet's 1.11 s
> overlaps TinyCLIP's 2.28 s rather than adding to it. Today MoveNet is **free** — fully
> hidden — which is consistent with a 2.33 s stage against 2.28 s of TinyCLIP.
>
> This makes the sequencing sharper, not softer. MoveNet is not a cost to remove now; it
> is a **floor**. Quantizing TinyCLIP buys real time only down to 1.11 s per photo, and
> below that MoveNet becomes the critical path and the delegate/runtime work is the only
> remaining lever. Anyone setting a target for M3 should treat 1.11 s as the wall until
> the pose model or the runtime changes too.

Neither model reloaded during the batch (`reloads:0`), so the 400-run interpreter
retirement never fired at this size and is not contaminating these numbers.

---

## Reading the rest of it honestly

```
cache-load           21 ms / 3,000
candidate-probe  53,983 ms / 3,000   hits:0
candidate-rank    4,046 ms / 3,000
deep-analysis   148,837 ms / 64
total           207,090 ms / 3,000
```

**`candidate-probe` at 54 s is a cold-cache artifact, not a regression.** `hits:0` — the
probe cache was empty because the signal-version fix in `ce88279` correctly invalidated
every pre-change entry. That is the fix working. It is a one-off and should fall back to
near-zero on the next build; the 207 s total against the 148 s baseline is this and
nothing else.

**The awaited numbers must not be summed.** They include queue wait, and photos are
analysed concurrently, so per-photo awaited time is inflated by however many are in
flight:

```
tinyclip.awaited-steady   mean 13,231 ms      <- 82% of this is queue wait
tinyclip.model-inference  mean  2,280 ms      <- the real cost
```

Anyone adding `proxy-create + perceptual + face-detect + quality-decode + movenet +
tinyclip` awaited means would get 19.1 s per photo against a stage that took 2.33 s. The
emitted line carries `analysis-note="...do not sum awaited phases"` for exactly this
reason.

The one awaited figure still worth attention is `quality-decode` at mean 2,131 ms with a
p95 of 9,118 ms. Even discounting queue wait, a p95 of nine seconds on a *pixel decode*
is out of line with the 1280 px analysis proxy it is supposed to be reading, and it sits
next to the 29 MB single allocation that threw `OutOfMemoryError` during this same build.
Both point at a full-resolution decode somewhere off the proxy path.

---

## What this changes

1. **M3 is unblocked and correctly aimed.** Quantize TinyCLIP first — the fidelity gates
   in `docs/EXPERT-PLAN.md` §8 apply as written.
2. **MoveNet sets the floor, and argues for the runtime work rather than quantization.**
   It is already int8 and still costs 1.11 s, so quantization cannot fix it — the ceiling
   here is the CPU/delegate path, which is also where the `fast-tflite` arena leak lives.
   Because it runs concurrently with TinyCLIP it costs nothing on the wall *today*, but
   it caps what M3 can win: **quantizing TinyCLIP buys time only down to 1.11 s/photo.**
   A target below that is unreachable without touching the pose model or the runtime.
3. **The candidate budget of 64 is explained.** At 2.33 s/photo, the plan's target of
   96–192 candidates is 3.7–7.5 minutes of deep analysis. §14's budget is unreachable
   until this stage is faster, exactly as sequenced.
4. **A 29 MB allocation threw OOM during this build** and the app caught it and carried
   on. Some photo's analysis silently degraded and nothing reports which. Tracked
   separately.

---

## Reproducing

The instrumentation ships on, is cheap, and emits through the existing
`[album-build-timing]` line. Build an album and read logcat:

```
adb logcat | grep album-build-timing
```

---

## Addendum: the review flow could not record 62% of its own answers

Measured the same day, on the same pulled index, with
`scratch/face-anchor-coverage/measure.ts --index <face-index.json>`:

```
people 2226   faces 15784   clusters with 2+ faces 921
NO ANCHOR: 1433 people (64.4%) holding 4062 faces (25.7%)
  sizes: 1 face 903, 2-3 292, 4-9 160, 10+ 78
REVIEW (limit 60): 60 offered, 37 refused (61.7%)
  first-5 3 refused, first-20 18 refused
  refused pairs would have fixed 186 photos; face counts 50+12, 51+12, 38+7, 57+6
```

A user judgement is stored against an ANCHOR ASSET, because person ids are rebuilt on
every recluster. An anchor has to be a photo only one cluster claims — and since two
faces in one photo are cannot-linked, that means a photo where the person is the *only*
detected face. **A person who is never photographed alone has no anchor**, and the app
answers "we can't remember that" and drops the correction.

**Eighteen of the first twenty questions were refusable.** That matters more than the
raw rate, because of the other thing measured today: there are **zero** unblocked cluster
pairs above 0.52 linkage. Every remaining split in this library is held by co-occurrence,
so the review queue is the *only* remaining lever — and most of its questions could not
be recorded.

Worth noting the agent that built the fix measured a synthetic library first and reported
honestly that its own numbers did not support the "people who matter most" framing: 6.9%
of faces affected, no refused repair worth more than 1–2 photos. On the real index it is
25.7% of faces and 78 anchorless people holding 10+ faces each. The synthetic model
under-fragmented and understated it, which the agent predicted it would.

The fix lets an anchor name *which face* inside a shared photo, resolved only when the
winning face clears the assignment bar and beats the runner-up by 0.15 — a margin
measured against confusable relatives, not chosen. Old constraints load unchanged.

### Measured after the fix, on the same library

The numbers above were the BEFORE half, and only the before half — `face-index.json`
carries person centroids and asset ids but not individual faces, and a face anchor is a
claim about *which* face in a shared photo. `anchorFor` declines to guess without them,
so a run given only the index silently reports the old rule's answer no matter which
code is checked out. The harness now takes `--observations` and reports both:

```
node --experimental-strip-types scratch/face-anchor-coverage/measure.ts \
  --index face-index.json --observations face-observations.jsonl
```

```
                          BEFORE            AFTER
people with no anchor     1450 (64.6%)      9 (0.4%)
faces they hold           4096 (25.9%)      9 (0.1%)
review refused            43/60 (71.7%)     6/60 (10.0%)
  of the first 20         17                6
  of the first 5          3                 0
photos at stake           191               6
refusals worth 4+ photos  15                0
largest refusal           21 photos         1 photo
```

Every one of the nine people still without an anchor holds exactly **one** face, and the
six residual refusals are worth one photo each. The substantial repairs — the 15 pairs
worth four or more photos, the 21-photo one at the top — are all now recordable.

Two cautions on reading this. The before figures differ slightly from the run in the
section above (71.7% against 61.7%) because that run used the previous day's export, at
2,226 people rather than 2,244; the conclusion is unchanged but the exact rate moves with
the library. And these faces are compared in **raw** space on purpose:
`centeredForClustering` is a no-op while `USE_CENTERED_CLUSTERING` is false, so
`embeddingMean` is never set and the stored centroids are raw. Centering the faces here
would compare them against centroids in a different space and quietly invalidate the
whole table.
