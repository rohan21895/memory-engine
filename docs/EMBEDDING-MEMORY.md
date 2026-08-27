# A third of the heap is face embeddings, and nothing ever gives it back

Measured 2026-08-27 on the owner's real library — 17,768 faces, 512 dimensions — with
`scratch/embedding-memory/measure.js`.

```
number[]  (as the app holds them):  89.5 MB   (5,039 bytes/face)
Int8Array (as they arrive on disk): 15.5 MB     (872 bytes/face)

ratio 5.8x   saving 74.0 MB
```

> **Correction, and it removes this document's original headline.** The first version
> said the embeddings were "a third of the 268 MB the process is allowed", quoting the
> `OutOfMemoryError`'s `growth limit 268435456`. That was wrong: **268 MB is the ART Java
> heap, and Hermes allocates its JS heap separately.** The 89.5 MB of `number[]`
> embeddings is not inside that limit and cannot have contributed to that crash. (Nor
> could a bitmap: since Android 8 `Bitmap` pixels live in native memory, also outside
> ART. See the addendum in `DEEP-ANALYSIS-TIMING.md`.)
>
> So this is **not** a headroom argument for the album-build OOM, and the two should not
> be reasoned about together. What survives is simpler and still worth fixing: 89.5 MB of
> process memory, pinned for the lifetime of the app, holding data whose entire
> information content is 9 MB. On a phone, resident set size is what gets an app killed
> in the background — it just is not what threw that particular exception.

## Why they cost that much

`FaceObservation.embedding` is `number[]` (`faces/types.ts:5`). It is built in
`dequantizeEmbedding` (`faces/face-index.ts:838`):

```ts
const signed = new Int8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
return Array.from(signed, (component) => component / 127);
```

512 bytes on disk become 512 boxed doubles in memory. The information content is
unchanged — every value is `k/127` for an integer `k` in [-127, 127].

## And nothing releases them

`observationsLoaded` is assigned `false` exactly once, at its declaration
(`face-index.ts:1333`). There is no release path anywhere. Once any of these has run,
the 89.5 MB is resident for the rest of the process lifetime:

| caller | what it is |
|---|---|
| `runBuild` | the face scan |
| `suggestedFaceMerges` | building the merge review queue |
| `recordConstraint` | the user answering a merge question |
| `reclusterIfCalibrationChanged` | recalibration |
| `clearFaceConstraints` | forgetting answers |

## What this does *not* mean

It is unrelated to the album-build OOM, on two independent grounds.

First, the album build never loads face embeddings at all: `selection/` does not import
`face-index`, `familiarPersonPredicate` reaches only `getPeople()` which reads the small
file, and the `.embedding` references in `build-album.ts` are perceptual and TinyCLIP
ones.

Second — and this is the part that killed the headroom theory — the embeddings are in a
different heap from the one that overflowed. The crash reported the ART Java heap;
Hermes' JS heap is allocated separately. So even when the 89.5 MB is resident, it is not
occupying any of the 268 MB.

The two problems are genuinely separate. This one is about resident set size and
background kills; that one is a 27.63 MiB `byte[]` on the Java side.

## Measurement caveat, worth repeating because it bit this measurement

`process.memoryUsage().heapUsed` **understates a typed array by an order of magnitude**,
because the backing store is allocated outside the JS heap. Measuring only `heapUsed`
reported Int8Array at 193 bytes for 512 dimensions — impossible on its face, and it would
have claimed a 26x saving instead of the real 5.8x. The harness counts
`heapUsed + external`.

This is V8, not Hermes. The ratio is the finding; the absolute constant is indicative.

## Options, laziest first

1. **Does anything need them resident as long as they are?** A release path is the
   smallest diff, but it is not free of hazard: dropping observations while
   `observationsDirty` would lose scan work, and clearing the array between an
   `await ensureObservations()` and its reader would silently yield an empty library.
   Any release needs a guard, not just a free.
2. **`Float32Array`** — about 2x. Not lossless: `k/127` rounds differently in float32
   than float64, so clustering shifts in the last bits. The merge sweep was deliberately
   kept bit-identical (`252a07b`); this deserves the same care and an explicit decision.
3. **`Int8Array` with the scale applied at the end of each dot product** — the full 5.8x,
   and integer multiply-accumulate is faster than float. Touches every similarity
   function in `face-cluster.ts`.
4. **`expo-sqlite` with binary embeddings** (the original M1) — incremental queries
   instead of all-or-nothing loads. Recommended by `docs/CX-21-PLAN-AUDIT.md`. Largest
   change; only worth it if 1–3 do not settle the question.

Options 2 and 3 both change stored groupings in the last bits, so either needs a
`CLUSTER_CALIBRATION` bump to force one clean rebuild rather than letting old centroids
and new arithmetic disagree silently.

## Decided: option 3, and it needed no calibration bump

`FaceObservation.embedding` is now `number[] | Int8Array` (`faces/types.ts`). A face loaded
from disk stays as its 512 bytes; a face fresh out of the detector is still `number[]`,
because it has not been quantized yet. Everything that reads a component goes through
`dequantized` (`faces/face-cluster.ts`).

**The sentence above about needing a `CLUSTER_CALIBRATION` bump was wrong, and correcting
it is the point of this section.** It assumed option 3 meant the obvious int8 scheme:
accumulate an INTEGER dot product and apply `1/127²` once at the end. That scheme is
actually *more* accurate than what ships — an integer dot product over 512 dimensions
maxes at 8.26M and is exact in float64 — and it is still the wrong thing to adopt, for
the reason `252a07b` recorded: the merge candidate queue settles exact ties by `pairKey`
and the sweep is greedy, so a one-bit disagreement can reorder a merge. That commit paid
for a second full dot product on every surviving pair rather than accept it.

The same care is available here for 2 KB. Every stored component is `k/127`, so a
256-entry `Float64Array` holds every double the old `Array.from(signed, (c) => c / 127)`
could produce, computed by that same division. Expanding a byte through the table returns
the identical bit pattern, so **no arithmetic anywhere sees a different value than it saw
before** — no new rounding, no reassociation, no bump.

Verified rather than argued, on the owner's real library
(`scratch/embedding-memory/int8-equivalence.ts`):

| form | components differing from baseline | people | fused impostors | pairs newly joined | pairs newly split |
|---|---|---|---|---|---|
| baseline `c/127` | — | 2,253 | 0 | — | — |
| **int8 via the table** | **0 of 9,097,216** | **2,253** | **0** | **0** | **0** |
| float32 (option 2) | 8,453,790, worst 4.9e-8 | 2,253 | 0 | 0 | 0 |
| one ulp on every component | 8,453,790, worst 2.2e-16 | 2,253 | 0 | 0 | 0 |

Read the bottom two rows as the control they are, not as permission. They say that *this*
library, clustered in *one batch*, has no tie tight enough for a last-bit difference to
reorder a merge — 37,125 same-photo impostor pairs and not one of them fused under any
form. They do not say the next library has none, and the device clusters incrementally
across many batches where the tie surface is different. The int8 row does not depend on
that luck: it is zero because there is nothing to disagree about.

And the guard that makes the zeros mean anything: the same comparison reports
`joined=394` when a single face is moved by hand, and `joined=90,043 split=949,210` for a
4-bit quantization of the same faces. It is not blind.

Measured saving, holding whole `FaceObservation` objects the way `index.observations`
holds them: **82.3 MB → 18.5 MB, 4.4x, 63.7 MB of resident set.** (The bare vectors are
the 5.8x above; the object carries an assetId, a kind, a flag and a time either way.)
Full recluster of 17,768 faces is at parity, 10.2–19.7s for `number[]` against 10.8–14.7s
for int8 over interleaved rounds — but only because `dequantized` builds its array with
`Array.from`; an index-filled `new Array(512)` measured ~40% slower for identical
arithmetic, and that trap is commented at the call site.

**Option 1, the release path, is not being shipped and should not be.** It was the
laziest option when the resident cost was 89.5 MB. At 18.5 MB it is not worth its two
hazards — dropping while `observationsDirty` loses scan work, and clearing between an
`await ensureObservations()` and its reader silently yields an empty library. Option 4,
`expo-sqlite`, remains open and is now a query-granularity argument rather than a memory
one.

## Reproducing

```
node --expose-gc scratch/embedding-memory/measure.js <face-observations.jsonl>

cd apps/mobile && node --experimental-strip-types \
  ../../scratch/embedding-memory/int8-equivalence.ts \
  --observations <face-observations.jsonl> [--order baseline,int8,baseline,int8]
```
