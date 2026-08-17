# Decision: an uncalibrated sharpness threshold may rank, and may not eliminate

Closes issue #22. Taken 2026-08-17.

---

## The situation

`packages/ranking-engine/memory_engine_ranking/fusion.py` carried

```python
DEFAULT_SHARPNESS_FLOOR = 0.08
```

and `eliminate()` discarded any frame below it. A discarded frame never
competes, is never shown, and never reaches an album. Nobody sees what went.

Three facts, all checkable in the repository as it stands:

- `workers/ingest` writes `quality = None` for every record. Nothing here has
  ever produced a sharpness value.
- No Laplacian-variance-to-unit normalisation exists anywhere in the tree.
- The contract declares `quality.sharpness` as a `Unit` on `[0, 1]`, and
  nothing maps a measurement onto it.

So `0.08` was a number against a scale that does not exist. That is not a
conservative setting or an aggressive one. It is undefined.

---

## What 0.08 actually eliminates — measured

Run `scripts/demo/make_library.py --stills 200 --no-video`, take the Laplacian
variance of every still, and apply candidate normalisations. All numbers below
were produced by running it, not estimated.

Raw Laplacian variance across the 200 generated stills:

| statistic | value |
|---|---|
| min | 11.6 |
| 5th percentile | 48.7 |
| median | 70.0 |
| 95th percentile | 92.5 |
| max | 99.2 |

Now normalise, and ask what a floor of 0.08 removes:

| normalisation | eliminated at 0.08 | median normalised |
|---|---|---|
| `min(1, lv/100)` | **0.0%** | 0.700 |
| `min(1, lv/500)` | **0.5%** | 0.140 |
| `min(1, lv/1000)` | **77.5%** | 0.070 |
| `log10(1+lv)/4` | **0.0%** | 0.463 |
| `tanh(lv/200)` | **0.5%** | 0.336 |

Same constant, same library, same images: between nothing and three quarters of
them, decided entirely by a divisor nobody has written down.

That is the whole argument. A gate whose behaviour is set by an unwritten
constant is not a gate; it is a coin whose bias is unknown.

---

## Why we did not simply calibrate it against the synthetic library

Issue #22 offers calibration as one of two ways out, and the brief that led to
this change said the synthetic library "has deliberately blurred variants". It
does not. There is exactly one blur call in `scripts/demo/make_library.py`:

```python
return image.filter(ImageFilter.GaussianBlur(radius=0.4))
```

applied uniformly to **every** still as an anti-aliasing pass. There is no junk
tail, no bimodality, and no degraded variant of anything. Total spread is 8.6x
between the least and most detailed image, with the single lowest value being
`IMG_LOWQ_01.JPG` — a low JPEG-quality still, not a blurred one.

Even if blurred variants were added, they would not settle it. Deliberately
blurring a median still measures:

| Gaussian radius | Laplacian variance | ratio to base |
|---|---|---|
| 0 | 61.09 | 1.000 |
| 1 | 6.71 | 0.110 |
| 2 | 1.54 | 0.025 |
| 4 | 0.76 | 0.013 |
| 8 | 0.61 | 0.010 |
| 32 | 0.54 | 0.009 |

Clean separation — on flat procedurally-drawn vector shapes. Camera blur is not
that:

- **Motion smear is directional**, and a Laplacian responds differently to a
  streak than to a symmetric defocus.
- **Sensor noise raises Laplacian variance while destroying detail.** A
  high-ISO frame at a first birthday can measure *sharper* than a clean one.
  Synthetic images have no sensor.
- **JPEG ringing and generation loss** add high-frequency energy that is not
  detail.
- **The hard negatives cannot be synthesised at all.** A dim handheld shot of a
  child's first steps, a deliberate long exposure, a shallow-depth-of-field
  portrait whose background is blurred and whose subject is not. These are
  technically poor and are precisely the photographs a family would be most
  upset to lose. Nothing drawn with PIL stands in for one.

A floor calibrated on this library would **look** calibrated. It would have a
number, a fixture, a green test and a paragraph like this one behind it — and it
would still delete real photographs. Every defect this codebase has caught
looked exactly like that (see `docs/architecture.md`, "The one thing to know
first").

---

## The decision

**Elimination requires a determination. Ranking is what a measurement gets.**

- `is_black_frame` and `is_lens_obstructed` are booleans. Something upstream
  looked at the frame and concluded a fact whose meaning does not depend on any
  scale. They still eliminate.
- `sharpness` is a measurement on a continuum that this system has not defined.
  It no longer eliminates by default. `DEFAULT_SHARPNESS_FLOOR` is `None`.

Low sharpness is not thereby ignored. It carries the largest weight in the
default fusion profile (0.22), so a blurry frame scores low, sorts last, and
wins only when the alternative is nothing — which is the correct answer at the
tail of a GoPro card and the answer a hard gate destroys.

### Elimination is not removed, it is gated behind evidence

`SharpnessFloor` is a value type. Constructing one requires:

| field | why it is validated |
|---|---|
| `value` | a finite Unit; outside `[0,1]` it eliminates everything or nothing |
| `normalisation_id` | **a floor is a pair (threshold, normalisation), never a threshold alone** — the table above is what happens when it is not |
| `benchmark_id`, `measured_at` | a floor nobody can re-measure is a floor nobody can ever tell is wrong |
| `junk_total`, `junk_eliminated` | a floor that catches no junk is pure risk |
| `hard_negatives_total`, `hard_negatives_retained` | **must be equal** |

The last one is the point. A floor that eliminates even one hard negative is
refused outright, however much junk it catches. That is not a tolerance set to
zero out of caution; it is the asymmetry restated. Keeping a bad photo costs a
slot in an album. Losing a good one is unrecoverable, and unobservable — the
user never learns the photo was there.

Passing a bare float to `eliminate()` or `fuse()` raises `UncalibratedFloor`,
because `sharpness_floor=0.08` is exactly the shape the guess comes back in.

---

## What would let the floor come back

In order, and none of it is in ranking-engine:

1. **`workers/ingest` computes and writes `quality.sharpness`** against a stated
   normalisation (issue #22 item 1, Codex's territory). Until the mapping from
   Laplacian variance to `[0,1]` exists, no threshold means anything. The
   normalisation gets an id, and that id goes in `SharpnessFloor`.
2. **A benchmark set of real photographs** holding both halves: genuinely
   unrecoverable frames (lens cap, pocket shot, extreme motion blur) *and* the
   hard negatives listed above. `packages/eval-harness/` is where it belongs.
   The hard negatives are the difficult half and the reason the first half
   cannot be measured alone.
3. **Pick the floor from that**, record it as a `SharpnessFloor`, and pin it
   with a fixture.

Until then, nothing is being discarded on sharpness, which is the honest state
and — since ingest writes no sharpness at all today — also the state the system
was already in. The difference is that it is now true by construction instead of
by accident.

---

## Reproducing the numbers

```bash
python3 scripts/demo/make_library.py --out /tmp/sharplib --stills 200 --no-video --quiet
```

then Laplacian variance per still with the 4-neighbour kernel
`[[0,1,0],[1,-4,1],[0,1,0]]` over the greyscale image, variance taken over the
valid (non-border) region. The seed is fixed, so the table above is
reproducible.
