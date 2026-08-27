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
p95 of 9,118 ms. Even discounting queue wait, a p95 of nine seconds on a *pixel decode*
is out of line with the 1280 px analysis proxy it is supposed to be reading, and it sits
next to the 29 MB single allocation that threw `OutOfMemoryError` during this same build.
Both point at a full-resolution decode somewhere off the proxy path.

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
   on. Some photo's analysis silently degraded and nothing reports which. Tracked
   separately.

---

## Reproducing

The instrumentation ships on, is cheap, and emits through the existing
`[album-build-timing]` line. Build an album and read logcat:

```
adb logcat | grep album-build-timing
```
