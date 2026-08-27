# Honest state of the expert plan

**Two-minute status, 2026-08-27.** Details and evidence are in
[`EXPERT-PLAN.md`](EXPERT-PLAN.md).

The expert plan is no longer a linear M0→M9 queue. M4 and M6 were built and rejected,
M3's proposed optimization lost, M1 targeted an already-fixed stall, and several
numeric targets were invalid.

## Finished negative experiments

- **M3 TinyCLIP quantization:** full int8 is blocked by `DIV`. Mixed int8 reduced
  33.2 MB to 8.5 MB but ran 3.4x slower on the measured Mac (6.55→22.15 ms) with
  71 quantize/dequantize boundaries. The Mac latency may not transfer to ARM; the graph
  defects do. A clean source reconversion or different runtime remains open.
- **M4 multi-prototype identity:** flag off. Zero of 937 multi-face people fell below
  the calibrated intra-tile bar, and random partitions bought the same merges at the
  same risk. The premise is false on this library.
- **M6 facility location:** flag off. Removing it changed only 1, 0, and 1 photos of 24
  across three fixtures. Facility alone degraded coverage; the duplicate win was the
  shipped hard duplicate constraint.
- **DPP:** rejected on mechanism, not by A/B. The current similarity blend has no PSD
  guarantee, so `log det(L_S)` is not a valid objective for every subset.
- **Framing tie-break:** deleted after zero ties in 100,000 simulated pairs. This does
  not reject pose diversity or future multi-person crops.

## Rescoped and open

- **M1:** launch no longer atomically parses observations; the loader yields every 500
  rows. The live problem is 89.5 MB of resident `number[]` embeddings with no release
  path. Scope M1 to bounded queries and controlled lifetime.
- **M3:** the phone's `model-inference` value is an awaited JS span containing kernel
  time, scheduling delay, and contention at concurrency six. The next work is runtime/
  environment isolation. FP16 CPU was never run; fast-tflite cannot expose XNNPACK's
  required `FORCE_FP16` flag.
- **M0:** audit, manifest, fixtures, and two standing gates exist. Eyes-open is blocked
  because the repo has no eligible real ML Kit open/closed pair set.
- **M2:** durable versioned deep signals and resumable Tier A/B/C jobs are not built.
- **M5:** durable duplicate/burst/moment/event hierarchy, reservations, and a measured
  candidate budget remain open. Top-64 is already diversity-aware but precedes moments.
- **M7:** pairwise labels are captured, but no learned checkpoint exists. It must beat
  the shipped zero-shot TinyCLIP axes plus rules, not rules alone.
- **M8:** multi-person pose/segmentation, group-photo truth, and crop evaluation remain
  open; no model dependency is installed.
- **M9:** chronological presentation and preference capture exist; learned taste,
  story roles, layout replacements, and held-out edit improvement do not.

## Actual order

Diagnose M3 first; do only the M1 storage foundation M2 needs; then build M2 and measure
M5's budget. Collect M7 labels now and train when event-split data is sufficient.
Prepare M8 truth/model work independently but integrate within an M3-measured finalist
budget. Build M9 after M5 supplies durable moments and M7 proves a preference signal.
M4 and M6 are not dependencies.

## Targets to retire

- `≤0.8 s/photo after int8` is below the current-path floor: MoveNet's concurrent span
  is about 1.111 s even with free TinyCLIP.
- `≤1.4 s after FP16 CPU` cannot be assigned to a backend the wrapper cannot engage.
- `<150 ms embedding load` uses the wrong path and contradicts bounded access.
- `≤5 s warm / ≤2 s repeat` is not an M5 gate while missing signals rerun.
- `≥95% eyes-open` has no eligible real-pair denominator.
- Tier C in “a few charging sessions” is not numeric.
- `60% key-person fragmentation vs 2,237 clusters` is dimensionally invalid.

Keep face-pair drift, degradation, and TinyCLIP fidelity gates, always with their
dataset, denominator, and vacuity guard.
