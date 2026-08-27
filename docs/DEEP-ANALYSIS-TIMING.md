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
inference per photo** — 48% of the same wall. A quantized 2.9 MB model taking half the
time of a 33 MB float32 ViT says the constraint is the CPU itself, not model size, and
that argues for the delegate/runtime work rather than quantization alone.

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
p95 of 9,118 ms.

## Addendum: the 29 MB allocation was not a decode

The first reading of that p95, written above, was that it sat next to the 29 MB
`OutOfMemoryError` from the same build and that both pointed at a full-resolution decode
off the proxy path. That reading is wrong, and the arithmetic settles it without a
rebuild.

**28,975,795 = 5 x 5,795,159, and 5,795,159 is prime.** A bitmap's byte count is
`rowBytes x height`, so it is a multiple of 4 for ARGB_8888 and of 2 for RGB_565. This
number is odd. It cannot be a bitmap pixel buffer of any Android config. ART reports a
`byte[]` as `12 + length`, which makes this a **27.63 MiB byte array** — a
data-dependent length, i.e. a payload, not a raster.

Two more things follow from the message itself. `growth limit 268435456` is the ART Java
heap, and since Android 8 `Bitmap` pixels live in native memory, not there. Hermes
allocates its JS heap separately too, so the ~89 MB of resident `number[]` face
embeddings is not in that 268 MB either. The build was at ~244 MB of **Java** heap, and
whatever filled it was Java-side bytes.

Tracing the album path agrees. Every per-photo decode reads the shared proxy:
`prepareCandidateAnalysisProxy` bounds the original with `Image.loadAsync(maxWidth: 1280,
maxHeight: 1280)` (Glide subsamples during decode, and with `centerInside`'s MEMORY
rounding a 4032 px frame decodes at 1008 px), and quality, face detection, MoveNet,
TinyCLIP and the perceptual fingerprint are all handed `proxy.uri` with `proxy.width` /
`proxy.height`. `detectFaces` then short-circuits entirely, because the proxy is already
a `file://` image inside `MAX_DETECTION_EDGE`.

The p95 has a simpler explanation that costs no heap: `measureImageQuality` runs a JS
JPEG decode plus four full-buffer passes over ~200k pixels on the one JS thread, six
photos in flight, and `measureAwaited` starts its clock at the call.

**Why this is still open.** The allocation has no site because six independent catches on
one photo's analysis path swallowed their errors and returned `undefined` / `{}` / `[]`.
The next build says so out loud:

```
analysis-degraded={photos:3/64,proxy-create:0,perceptual:0,face-detect:1,quality-decode:2,movenet:0,tinyclip:0,oom:1}
[album-build-degraded] quality-decode count=2 oom=1 first="java.lang.OutOfMemoryError: Failed to allocate a ..."
```

If `oom` is 0 on a build that logs an `OutOfMemoryError`, the allocation is outside the
analysis pass — and the file reads are then the place to look, because
`readAsStringAsync` allocates a `byte[]` of exactly the file's length (Kotlin's
`InputStream.readBytes()` sizes its `ByteArrayOutputStream` from `available()` and then
copies it), which is the one shape in this app that produces an arbitrary length like
28,975,783.

---

## What this changes

1. **M3 is unblocked and correctly aimed.** Quantize TinyCLIP first — the fidelity gates
   in `docs/EXPERT-PLAN.md` §8 apply as written.
2. **MoveNet argues for the runtime work, not just quantization.** It is already int8 and
   still costs 1.11 s. Quantization cannot fix a model that is already quantized, so the
   ceiling here is the CPU/delegate path — which is also where the `fast-tflite` arena
   leak lives.
3. **The candidate budget of 64 is explained.** At 2.33 s/photo, the plan's target of
   96–192 candidates is 3.7–7.5 minutes of deep analysis. §14's budget is unreachable
   until this stage is faster, exactly as sequenced.
4. **A 29 MB allocation threw OOM during this build** and the app caught it and carried
   on. Some photo's analysis silently degraded and nothing reported which. It still
   catches and still carries on — it no longer keeps quiet. See the addendum above for
   what the number rules out and what the new counters will say.

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
