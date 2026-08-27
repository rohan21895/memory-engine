# A third of the heap is face embeddings, and nothing ever gives it back

Measured 2026-08-27 on the owner's real library — 17,768 faces, 512 dimensions — with
`scratch/embedding-memory/measure.js`.

```
number[]  (as the app holds them):  89.5 MB   (5,039 bytes/face)
Int8Array (as they arrive on disk): 15.5 MB     (872 bytes/face)

ratio 5.8x   saving 74.0 MB
```

The app's heap ceiling on this device is **268 MB** — that figure is not an estimate,
it is what the `OutOfMemoryError` printed: `target footprint 268435456, growth limit
268435456`. So the embeddings alone are a third of everything the process is allowed.

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

## What this means for the album-build OOM

The album build itself does **not** load face embeddings — `selection/` never imports
`face-index`, and the `.embedding` references in `build-album.ts` are the perceptual and
TinyCLIP embeddings, not identity ones. So this is not the direct cause.

It is the headroom. The crash was:

```
Failed to allocate a 28975795 byte allocation with 25165824 free bytes
and 24MB until OOM, target footprint 268435456, growth limit 268435456
```

A 29 MB request arriving with 24 MB left. If the owner had scanned or opened the merge
review earlier in that session — and the review is exactly what he is being asked to use
— then 89.5 MB of the 268 MB was face embeddings that nothing was reading at the time.

That does not make the 29 MB allocation innocent. A single 29 MB request is about 7.2
megapixels at 4 bytes each, which is a full-resolution decode rather than the 1280 px
analysis proxy, and it needs fixing on its own terms. But the build may be running with
far less room than anyone assumed.

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

## Reproducing

```
node --expose-gc scratch/embedding-memory/measure.js <face-observations.jsonl>
```
