# Does the pose framing tie-break ever fire?

Measurement only. No selection rule changed.

`3f2fb4b` wired `pose-framing.ts` into selection and shipped with an explicit
caveat: *"this is not measured on a real library... nobody has shown it changes
a picture the owner would care about."* This is that measurement.

Reproduce from the repository root with Node 22 or newer:

```sh
node --experimental-strip-types scratch/framing-tiebreak-rate/measure.ts /path/to/obs.jsonl
```

---

## The answer: it never fires

**0 of 100,000 simulated near-duplicate pairs reached the tie the code needs.**
Not "rarely". The condition is unreachable by construction on any pair of frames
the device measured separately.

The signal is computed for every deeply analysed photo. It has never changed one.

---

## Where framing touches an ordering

Exactly one place, and the commit is accurate about it. `BodyCoverage` is read
by a single call site:

```
build-album.ts:743      bodyCoverage(keypoints, scores, analysisWidth, analysisHeight)
build-album.ts:835      bodyCoverage: coverage        -> onto the photo
select-best-shots.ts:601  framingTieWinner([...eligible].sort(compareCandidates))
```

`framingTieWinner` may promote a later frame of a take over the current leader.
It is not summed into `enhancedQualityScore`, never reaches
`compareRankedTakes`, and never changes which photos are eligible. Everything
downstream — the planner, the quality floor, the album — sees the change only as
a different media id winning one take.

Five conditions must all hold for that promotion to happen:

| | condition | where |
| --- | --- | --- |
| G1 | the take has two or more eligible frames | `rankTake` |
| G2 | leader and challenger tie on `qualityBand`, `smileRank`, **`quality`**, `pixels` | `compareMeasuredSignals` |
| G3 | **both** frames have `analysis.faceCount === 1` | `singleSubjectFraming` |
| G4 | both have a readable `bodyCoverage` (`framing !== "unknown"`) | `compareFramingCompleteness` |
| G5 | the challenger is strictly better framed | `compareFramingCompleteness` |

G2 is where it dies.

---

## G2: the tie is exact double equality, and nothing rounds

`compareMeasuredSignals` compares the raw `quality` double. Nothing on the path
from `enhancedQualityScore` to that comparison rounds it — `roundScore` is
applied only to the album's **pool** output at `select-best-shots.ts:304`, after
the winner has already been chosen, and the only production caller
(`build-album.ts:867`) hands over analysis computed fresh in memory, never
rehydrated from a rounded store.

So G2 asks whether two independently measured images produce the *same
IEEE-754 double*. They do not, and they are not close to it:

| burst spread between frames | pairs | exact ties | median gap | smallest gap seen |
| ---: | ---: | ---: | ---: | ---: |
| 5e-2 | 20,000 | **0** | 3.96e-3 | 4.02e-7 |
| 1e-2 | 20,000 | **0** | 7.96e-4 | 3.12e-8 |
| 2e-3 | 20,000 | **0** | 1.60e-4 | 2.85e-8 |
| 1e-4 | 20,000 | **0** | 7.99e-6 | 1.92e-9 |
| 1e-7 | 20,000 | **0** | 7.99e-9 | 1.75e-12 |

At a realistic 1% spread the closest of 20,000 pairs was still **8.24e7
representable doubles** apart. A tie needs zero. The last two rows are far below
anything a camera produces and are there to show the trend: tightening the
spread by five orders of magnitude does not produce a single tie.

The other route to a repeated double is `clamp01` saturation — two photos both
landing on exactly 0 or exactly 1. That happened 0 times in 20,000.

**The simulation is biased towards finding ties, and still found none.** The two
genuinely collision-prone inputs are held *identical* across both frames of a
take: `clippedFraction` (an integer count over an integer pixel count, very
often exactly 0) and `resolutionQuality` (identical whenever the dimensions
match, which inside a burst they always do). Only the continuously measured
inputs jitter. Reality is less favourable to a tie than this, not more.

**Sabotage guard.** A column of zeros proves nothing if the tie detector cannot
fire. Fed the same measurement on both sides it reports 20,000/20,000 ties.

### The one exact tie a real device does produce

Two byte-identical copies of one photo — a re-download, a WhatsApp copy — really
do tie: same pixels, same measurements, same double. This is the general case,
not a special one: identical measurements can only come from identical pixels,
and identical pixels also give identical keypoints. So the coverage matches too,
`compareFramingCompleteness` returns 0, and the leader stands. **0 of 20,000
duplicate pairs flipped.** Both branches are closed — either the frames differ,
and they cannot tie, or they do not differ, and framing has no opinion.

### Why this was not obvious

Both prior pieces of evidence for the feature manufactured the tie:

- `framing-tiebreak.test.ts` gives both frames **one shared `analysis` object**.
  Its own comment says why: *"equal to the bit rather than merely close — which
  is the only condition under which the tie-break fires."* That is true, and it
  is exactly the condition two separately measured frames never meet.
- The CX-19 corpus (`face-sharpness-policy-harness.ts:199`) assigns **one
  `quality` per take**, quantized with `Math.round(x * 100) / 100`, and never
  calls `enhancedQualityScore` at all. Its "312 replaced (13.5%)" figure — cited
  in the commit message as evidence — measures that quantization, not the
  shipping scorer.

Neither is wrong as a unit test or a sensitivity study. Neither is evidence the
condition occurs.

---

## G3: the single-person gate, on the owner's real library

Measured directly from `obs.jsonl` (17,768 detected faces over 7,174 photos that
have any face):

| faces in photo | photos | share |
| ---: | ---: | ---: |
| exactly 1 | 3,258 | **45.4%** |
| 2 or more | 3,916 | **54.6%** |

**81.7% of all faces in the library are in a photo where MoveNet's single-person
fit cannot speak for them.** The `faceCount === 1` guard is the right call — it
correctly refuses to present a claim about one body as a claim about the
photograph — but it also means the signal is inert on the majority of the
library's people photos before any tie is even considered.

For anyone reusing the CX-19 corpus: 29.1% of face-bearing photos here have
three or more faces, against the 52.9% that corpus assumed. The denominators are
not the same — CX-19 counted deep-analysis candidates including faceless ones,
this counts photos with at least one detected face — so treat it as a flag to
re-derive that corpus from `obs.jsonl` rather than as a like-for-like gap.

---

## The ceiling: what it would deliver if ties were free

Hand the tie over for nothing — both frames share one analysis object — and let
the real `selectBestShots` decide, with and without `bodyCoverage`:

| population | tied takes whose winner moved |
| --- | ---: |
| single-face photos only | **18.4%** |
| real library face mix (G3 applied) | **8.6%** |

So the mechanism works and is not timid. It is gated behind a condition that
never occurs. Multiply the two measured factors and the shipping rate is
0 × 8.6% = **0**.

---

## Direction of effect: it is a full-body preference

`compareFramingCompleteness` is a fixed total order. Enumerated over all nine
coverages the extractor can actually produce:

| coverage | beats |
| --- | ---: |
| full (clean) | 8 / 8 |
| threeQuarter (clean) | 7 / 8 |
| half (clean) | 6 / 8 |
| upper (clean) | 5 / 8 |
| upper (cut by frame) | 4 / 8 |
| head (clean) | **3 / 8** |
| head (cut by frame) | 2 / 8 |
| threeQuarter (cut at joint) | 1 / 8 |
| half (cut at joint) | 0 / 8 |

The order is depth-first, overridden only by `cutAtJoint` — which by
construction cannot occur above the hips (`LOWER_BODY = 2`). So a
head-and-shoulders portrait can **never** outrank a whole body that is not
severed at a joint. In the ceiling run every top move is `X -> full (clean)`.

**A clean close-up of one face loses to a clean full-body shot, always.** That is
the product risk in the brief, confirmed: were the gate ever opened, this rule
would systematically trade close-ups of the owner's daughter for wider shots.
The comparator's own docstring argues the opposite case — "a clean
head-and-shoulders portrait is a better picture than a full body sliced off at
the ankles" — and that specific case is honoured, but only against a body cut
*at a joint*. Against any clean body, depth wins.

---

## The 1.11 s question

`docs/DEEP-ANALYSIS-TIMING.md` measures MoveNet at 1,111 ms mean inference, 48%
of the deep-analysis wall. Two corrections before anyone acts on that:

1. **MoveNet is not on the critical path.** It runs inside the same
   `Promise.all` as TinyCLIP (`build-album.ts:690-727`), and TinyCLIP's own
   inference is 145,952 ms of a 148,837 ms stage — 98%. MoveNet's 71,113 ms
   overlaps it. Deleting MoveNet frees contended CPU; it does not remove 1.11 s
   per photo from the wall.
2. **Framing is not why MoveNet runs.** The same keypoints feed `makePose` /
   `clusterPoses` → `poseCluster`, which the planner uses as a real diversity
   key (`album-planner.ts:347-352`). That consumer is live and unaffected by any
   of this.

So this is not an argument for dropping pose inference. It is an argument about
one function that reads its output.

---

## Recommendation

**Delete `framingTieWinner` and its call site, or open G2 deliberately. Do not
leave it as it is.**

The current state is the worst of the three: it costs a `bodyCoverage` call per
photo, it carries a field through `build-album` into `select-best-shots`, it has
274 lines of test, and it has never once changed a photo. It also sits one
innocent-looking edit away from waking up — anyone who quantizes `quality`, adds
a tolerance to `compareMeasuredSignals`, or round-trips analysis through a store
turns on an untested full-body preference across the whole library, silently.

The two honest options:

- **Delete it.** Keep `pose-framing.ts` — `bodyCoverage` is correct, tested, and
  the letterbox handling in `toPhotoSpace` is a real piece of work worth
  keeping for whatever reads it next. Remove only the selection wiring. Smallest
  diff, restores the pre-`3f2fb4b` behaviour that the measurement says is already
  what ships.
- **Open it deliberately**, by replacing the exact-equality tie with a band
  (`|Δquality| < ε`) the way `qualityBand` already bands quality at 0.02. That
  is a real behaviour change on ~8.6% of near-duplicate takes and it must not
  ship before someone looks at changed pairs, because at that point the
  full-body preference above becomes live. The commit's own reasoning against
  loosening the condition still stands: the winner's quality is forwarded to the
  planner and the album's quality floor is derived from those winners, so a
  banded swap moves a number that decides eligibility, not just order.

If neither is worth doing now, delete it. A dormant rule with a measured
direction nobody has agreed to is not a neutral thing to keep.
