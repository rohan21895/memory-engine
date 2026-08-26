# CX-13 mobile album-selection audit

Audited at repository revision `24f6839` on 2026-08-26. This document traces the
mobile path that is actually invoked by `apps/mobile/App.tsx`; it does not treat
the Python album engine or roadmap status labels as evidence that behavior is
present in the mobile build.

The first nine sections are an implementation audit only. The final section is
the separately requested review-only proposal. No production selection code was
changed for CX-13.

## 1. Entry point and input evidence

The gallery adapter turns each device asset into a `PickedPhoto`. It carries the
asset id, source URI, dimensions, capture time when available, the local place
bucket, and every high-confidence face-cluster id already known for that asset:

> `personIds: personIdsForAsset(asset.id),`
>
> — `apps/mobile/src/import/GalleryGrid.tsx:96-110`

This is where the completed face scan reaches album creation. The selector does
not read the face index directly; it sees only these `personIds` plus the fresh
face detections described below.

The application always requests 24 photos:

> `const built = await buildAlbum(next, 24, {`
>
> — `apps/mobile/App.tsx:327`

`buildAlbum` deduplicates neither source paths nor content hashes. Exact repeated
ids are removed later, first by the candidate prepass (`candidate-prepass.ts:
148-155`) and again by `buildCandidates` (`select-best-shots.ts:304-312`).

## 2. The first fork: all-photo analysis or a 64-photo candidate gate

Two constants decide whether every picked photo gets heavy analysis:

> `export const CANDIDATE_PREPASS_THRESHOLD = 500;`
>
> `export const HEAVY_ANALYSIS_CANDIDATE_LIMIT = 64;`
>
> — `apps/mobile/src/selection/candidate-prepass.ts:6-11`

`buildAlbum` engages the gate only when `photos.length > 500`; exactly 500
photos take the uncapped path (`apps/mobile/src/build-album.ts:291-294`). On the
capped path it probes every photo, then passes only
`chooseHeavyAnalysisCandidates(probed)` into heavy analysis
(`apps/mobile/src/build-album.ts:307-345`). On the uncapped path, every input
photo is analyzed (`apps/mobile/src/build-album.ts:347-353`).

The prepass runs up to 32 probes concurrently and heavy analysis runs up to six
photos concurrently (`apps/mobile/src/build-album.ts:59-64`). Those are work
limits, not quality rules.

### 2.1 What the cheap probe measures

The probe asks `expo-image` for a 32 px image and generates a 4x3 BlurHash. It
decodes that hash to 16x12 grayscale pixels and derives whole-frame sharpness,
mean exposure, and clipped-pixel fraction:

> `const blurhash = await Image.generateBlurhashAsync(image, [4, 3]);`
>
> `return qualityFromBlurhash(blurhash);`
>
> — `apps/mobile/src/selection/candidate-quality-probe.ts:35-53`

> `sharpness: sharpnessFromPixels(gray, DECODE_WIDTH, DECODE_HEIGHT),`
>
> `exposure: exposure.exposure,`
>
> `clippedFraction: exposure.clippedFraction,`
>
> — `apps/mobile/src/selection/candidate-quality-probe.ts:123-140`

The code itself states that this sharpness is a relative prepass prior, normally
about 0.04-0.06, not a calibrated absolute measurement
(`candidate-quality-probe.ts:21-33`). A probe failure returns an empty object,
not a rejection (`candidate-quality-probe.ts:38-53`). Missing values later use
neutral defaults.

### 2.2 The prepass quality score

Each unique id receives this exact score:

```text
Qcheap = 0.55 * sharpness
       + 0.20 * (1 - min(1, abs(exposure - 0.5) * 2))
       + 0.15 * (1 - clippedFraction)
       + 0.10 * min(1, log2(max(1, width * height)) / 24)
```

This is the literal arithmetic at
`apps/mobile/src/selection/candidate-prepass.ts:192-210`. Missing sharpness and
exposure become 0.5; missing clipping becomes 0
(`candidate-prepass.ts:301-305`). Dimensions that are absent or invalid produce
zero pixels.

### 2.3 The prepass diversity keys and priority

Timed photos are sorted chronologically and divided into
`min(40, limit, ceil(sqrt(timed_count)))` equal-count buckets. Untimed photos
have no time bucket (`candidate-prepass.ts:157-188`). Place is the trimmed
`photo.placeKey`; missing place gets no place term (`candidate-prepass.ts:
219-227,292-299`).

The content key is not an image or semantic embedding. The 16x12 decoded
BlurHash is block-averaged to a 4x3 grid, and each cell is quantized to one of
eight grayscale levels (`candidate-prepass.ts:35-80`). Photos with the same
coarse layout of light share a content key even if the subjects and moments are
different.

At every greedy step the score is:

```text
prepass priority = Qcheap + time coverage + place coverage + content coverage

time:    unavailable -> 0; first -> 1.10; repeat -> 0.16 / (count + 1)
place:   unavailable -> 0; first -> 0.45; repeat -> 0.10 / (count + 1)
content: unavailable -> 0; first -> 0.90; repeat -> 0.14 / (count + 1)
```

The formulas are at `candidate-prepass.ts:213-255`. The highest current priority
wins. Exact ties use higher `Qcheap`, then lexical media id
(`candidate-prepass.ts:114-143,282-289`). The returned array is restored to
input order, so its order is not the greedy order (`candidate-prepass.ts:145`).

Pins are selected before the greedy loop, ordered by `Qcheap`, but are truncated
to the same hard limit. The comment is explicit: “The safety cap still wins if
more than the limit are pinned” (`candidate-prepass.ts:104-112`). Exclusions are
not read by the prepass. Most importantly for the completed face scan,
`personIds` are not read anywhere in this module.

## 3. Heavy analysis on the surviving set

Every survivor gets one temporary JPEG proxy with a 1280 px long-edge bound and
JPEG quality 0.94 (`candidate-quality-probe.ts:5-15,67-108`). Proxy failure does
not remove the photo; it reaches selection with empty model evidence and any
prepass quality that exists (`build-album.ts:368-384`).

On a valid proxy, `buildAlbum` concurrently runs:

- ML Kit face detection with accurate mode, landmarks, classification, minimum
  face size 0.08, and no tracking (`faces/face-detector.ts:121-128`);
- calibrated whole-frame image quality, plus a subject region around the largest
  detected face (`build-album.ts:393-428`);
- MoveNet single-person pose (`build-album.ts:426`);
- TinyCLIP image embedding and six zero-shot contrasts: aesthetic, composed,
  clean frame, sleeping/awake, embrace context, and screenshot/document
  (`ml/tinyclip.ts:312-347`).

The ordinary path also calls `getModel().run`. `getModel` currently returns
`StubOnDeviceModel`, not the model-health graphs (`ml/index.ts:18-22`). Its
embedding is an 8x8 centered-luma thumbprint plus RGB histograms; if that fails,
it returns a deterministic pseudo-random vector derived from the URI
(`ml/stub-model.ts:46-88,91-166,207-220`). On capped builds this call is skipped
entirely and an empty embedding is supplied (`build-album.ts:409-427`), so the
TinyCLIP embedding becomes the fallback used for near-duplicate grouping
(`build-album.ts:191-206,494-497`).

MoveNet poses need at least four usable joint-angle dimensions. Poses are
clustered mirror-invariantly at a 22 degree RMS threshold; unavailable poses get
label -1 (`selection/pose.ts:36-41,94-120,118-147`).

### 3.1 Signals assembled for selection

Face boxes are normalized against the proxy dimensions. A face is marked cut if
its box lies within 1% of any edge (`build-album.ts:44-45,228-243`). The analysis
object contains face count, largest face-area ratio, cut status, quality fields,
metadata screenshot classification, and a category (`build-album.ts:208-269`).

Category classification is:

- zero faces: `scene`;
- any faces but largest under 1% of the frame: `detail`;
- three or more faces: `group`;
- two faces: `couple`;
- one face at least 3.5%: `portrait`; otherwise `detail`.

Those thresholds are at `selection/quality-signals.ts:33-35,46-72`.

The metadata screenshot/document check rejects any aspect ratio at least 3.5,
or a filename containing screenshot/screen-shot or ending in `.png` when the
aspect ratio is within 0.04 of a common screen ratio
(`quality-signals.ts:35-44,74-102`). `buildAlbum` ORs that result with TinyCLIP's
`screenshotDocument >= 0.06` result (`build-album.ts:499-505` and
`ml/tinyclip.ts:27`).

## 4. Per-photo quality fusion and take collapse

`selectBestShots` first removes duplicate ids. Faces smaller than 0.5% of the
frame are ignored for expression and edge-cut decisions. Eye quality is the
minimum known eye-open probability across significant faces; smile is the
maximum known smile probability (`select-best-shots.ts:13-20,304-349` and
`quality-signals.ts:104-127`).

### 4.1 Enhanced quality score

Available components are renormalized by their available weight, then the
category's cut-face penalty is subtracted and the result is clamped to [0,1]:

> `return clamp01(weightedTotal / availableWeight - cutPenalty);`
>
> — `apps/mobile/src/selection/select-best-shots.ts:754-777`

Resolution is `clamp01(sqrt(pixels / 12,000,000))`
(`select-best-shots.ts:780-782`). Exposure quality is one at 0.5 and decreases
linearly to zero at 0 or 1; clipping contributes `1 - clippedFraction`
(`select-best-shots.ts:784-798`). Sharpness falls back to the embedding-derived
thumbnail-detail value only when measured sharpness is missing
(`select-best-shots.ts:754-760`).

The exact category weights are:

| Category | sharp | resolution | eyes | smile | exposure | clipping | cut penalty |
|---|---:|---:|---:|---:|---:|---:|---:|
| portrait | 0.38 | 0.05 | 0.25 | 0.12 | 0.10 | 0.10 | 0.22 |
| couple | 0.36 | 0.05 | 0.27 | 0.12 | 0.10 | 0.10 | 0.18 |
| group | 0.42 | 0.08 | 0.24 | 0.05 | 0.11 | 0.10 | 0.10 |
| detail | 0.58 | 0.16 | 0 | 0 | 0.13 | 0.13 | 0.05 |
| scene | 0.55 | 0.18 | 0 | 0 | 0.14 | 0.13 | 0.03 |

These values are literal at `select-best-shots.ts:32-82`.

If there is no analysis object, the legacy score is 85% thumbnail detail and
15% resolution when both exist; detail alone, resolution mapped to 0.25-0.75,
or 0.5 otherwise (`select-best-shots.ts:724-739`). `buildAlbum` constructs an
analysis object even when proxy analysis fails, so this is not the normal mobile
build path.

### 4.2 Screenshot gate, near-duplicate takes, and within-take gates

Screenshot/document candidates are removed before take construction. This is an
absolute filter at this layer:

> `const eligibleCandidates = candidates.filter(`
>
> `  (candidate) => !candidate.analysis?.isScreenshotOrDocument,`
>
> `);`
>
> — `apps/mobile/src/selection/select-best-shots.ts:138-145`

Each remaining candidate joins the first existing take for which its embedding
has cosine similarity at least 0.92 to **every** member. Otherwise it starts a
new take (`select-best-shots.ts:355-377`). Missing/incompatible embeddings have
similarity zero, so each becomes its own take (`select-best-shots.ts:683-701`).

Within a take, the blink gate activates only if at least one frame has known
`eyesOpen >= 0.50`. It then removes frames with known `eyesOpen < 0.35`; unknown
eyes remain eligible (`select-best-shots.ts:380-401`).

The take winner is ordered by:

1. quality band `round(quality / 0.02)`, descending;
2. smile, descending, only for portrait/couple and only when known;
3. exact quality, descending;
4. source pixels, descending;
5. original input order;
6. media id.

This comparator is at `select-best-shots.ts:404-440`. Only the winner of each
take reaches the album planner; the other frames become swap alternatives.

## 5. Planner gates before scoring

`selectBestShots` passes one winner per take into `planAlbum`. The actual bridge
fields are listed at `select-best-shots.ts:159-189`: quality, time, place,
person ids, embedding/space, category, a newly constructed shot-group id,
pose family/cluster, pin/exclude flags, cut face, smile/eyes, screenshot flag,
and TinyCLIP axes.

The bridge does **not** pass `clippedFraction`, `faceExposure`, `faceSharpness`,
`headSharpness`, `hardRejected`, `hardRejectionReason`, or
`naturalExpression`, although `PlannerCandidate` defines all of them
(`album-planner.ts:14-46`). Therefore the planner's clipping, face exposure,
face sharpness, head sharpness, arbitrary hard-image, and natural-expression
branches are inert for the current `selectBestShots` call. Whole-frame
sharpness/exposure/clipping have already influenced the fused quality score;
they are not separate planner gates.

The effective planner target is the smaller of the requested count and the
number of take winners (`select-best-shots.ts:190`).

### 5.1 Absolute gates

Pins bypass all absolute and soft rejections. For non-pins, absolute rejection
order is:

1. user excluded;
2. `hardRejected`;
3. screenshot/document.

The code is at `album-planner.ts:210-230,702-706`. In the mobile bridge,
screenshots were already removed, `hardRejected` is absent, and exclusion can
come from the photo or `selectBestShots` options. `buildAlbum` does not pass the
options-level pin/exclude lists (`build-album.ts:515-517`), but per-photo
`pinned`/`excluded` flags are passed.

### 5.2 Soft gates and rescues

The default thresholds are quality 0.35, face sharpness 0.12, head sharpness
0.08, reject cut faces, clipped fraction at most 0.15, and face exposure at
least 0.06 (`album-planner.ts:87-94`). The implementation stops on the first
failure in this order: quality, cut face, clipping, face exposure, face
sharpness, head sharpness (`album-planner.ts:709-724`). On the current bridge,
only quality and cut face are populated.

The quality floor passed by mobile is not always 0.35. It is:

```text
keep fraction = min(1, max(0.5, requested_count / take_count))
quality floor = min(0.35, observed score at that keep fraction)
```

This guarantees at least half the takes, or enough takes to fill the request,
survive the quality gate (`select-best-shots.ts:266-302` and
`image-quality.ts:230-266`).

A soft failure is waived when the candidate is either:

- a rare moment: its shot group has one member, it has a valid time, and no
  other candidate lies within 30 minutes; or
- the best-quality photograph of a person for whom every surviving photograph
  has a soft failure.

The rescue order and mechanics are at `album-planner.ts:232-250,727-761`.
Because the mobile bridge assigns each winner `shotGroup:
take:<winner-media-id>`, every winner's shot group is a singleton at this stage
(`select-best-shots.ts:171`). A rescue can act only on candidates that survived
the 64-photo prepass and take collapse.

## 6. Planner score, coverage phases, and caps

After the gates, quality is converted into standing within category/comparison
class. The percentile midrank is blended with raw quality using a prior weight
of four:

```text
standing = (midrank * class_size + raw_quality * 4) / (class_size + 4)
```

See `album-planner.ts:12,764-792`.

Every other numeric axis is percentile-ranked across all surviving candidates;
ties get their midrank and missing values get 0.5
(`album-planner.ts:851-873`).

### 6.1 Greedy gain

The default gain weights are:

| Term | Weight |
|---|---:|
| quality standing | 1.00 |
| time coverage | 0.85 |
| place coverage | 0.50 |
| moment coverage | 0.35 |
| pose coverage | 0.55 |
| person coverage | 0.60 |
| redundancy penalty | -0.80 |
| smile percentile | 0.35 |
| composed percentile | 0.25 |
| aesthetic percentile | 0.55 |
| clean-frame percentile | 0.35 |
| mid-blink penalty | -0.60 |

The policy is at `album-planner.ts:87-124`; the literal sum is at
`album-planner.ts:427-468`. Coverage gain is `0.5 ** current_count`
(`album-planner.ts:929-930`). For a multi-person photo, person gain is the mean
of that decay over its people. A no-people photo uses a shared empty-person
bucket (`album-planner.ts:925-926`).

Category overrides increase portrait smile to 0.45, couple composed to 0.35,
group blink penalty to 0.75, and detail aesthetic/composed to 0.70/0.35
(`album-planner.ts:182-187`).

Times are divided across the observed span into `min(target, 24)` bins by
default; all unknown times share one unknown bucket
(`album-planner.ts:324-339,914-923`). Places use `placeKey` or one unknown
bucket. Moments union candidates with compatible embedding spaces, cosine at
least 0.80, and capture times no more than six hours apart; the final moment key
also includes face-count bucket 0-3 (`album-planner.ts:340-346,795-829`). Without
valid times, candidates do not union into moments.

Pose keys are the pose cluster when present. A missing pose becomes
`nopose:<media-id>`, which is unique and therefore receives first-use pose gain
for every such candidate (`album-planner.ts:347-352`).

The redundancy penalty starts above a calibrated free similarity. Its baseline
is at least 0.60 and may rise to the sampled median, capped at 0.93; the
denominator is `max(0.93 - free, 0.02)` (`album-planner.ts:831-849`). The closest
selected similarity is updated after each commit (`album-planner.ts:398-425`).

Blink penalty applies when the candidate is not classified as sleeping, is not
in the top 15% embrace-context percentile, and either has low absolute/relative
eyes-open evidence or lacks eyes evidence but has low awake evidence. The exact
conditions are at `album-planner.ts:453-467`. Sleeping means TinyCLIP sleeping
is greater than awake and above 0.04 contrast; its cap is
`max(1, floor(target * 0.20))` (`album-planner.ts:386-396`).

### 6.2 Selection phases and caps, in order

1. Pins are committed in media-id order, even if they exceed the target
   (`album-planner.ts:471-473`). The earlier prepass can already have truncated
   pins to 64.
2. Person floor: while slots remain, choose the photo covering the greatest
   number of people below `minPerPerson = 1`; ties use gain, then id
   (`album-planner.ts:475-500`). This sees only people present in surviving take
   winners.
3. Reserve non-people photos: `min(floor(target * 0.15), nonpeople available,
   target)` slots (`album-planner.ts:503-519`). The reserve is enforced only in
   the later fill loop, after pins and person-floor commits.
4. Per-person cap: if more than one distinct person exists, start at
   `max(1, ceil(target * 0.50))`; with zero or one person it is the whole target
   (`album-planner.ts:879-883`). A photo containing anyone already at the cap is
   blocked. If that would leave no eligible photo, the cap increases by one and
   the loop retries, so it is a preference that relaxes to fill the book
   (`album-planner.ts:520-547`).
5. Sleeping photos are skipped after their cap. This cap is not relaxed in the
   same block (`album-planner.ts:533-547`).
6. Shot cap starts at one per shot group; pose-family and body-pose caps start at
   two each. If nothing is fresh, family, shot, then pose caps are incremented
   until work can continue (`album-planner.ts:510-512,550-569`). In the mobile
   bridge, shot group and fallback pose family are unique per take winner.
7. Prefer candidates whose closest selected cosine is strictly below 0.92. If
   none exist, the rule does not block selection: it chooses the least-similar
   remaining candidate, then gain, then id (`album-planner.ts:572-584`).
8. Stop at target or when nothing eligible remains (`album-planner.ts:514-585`).

The planner returns selection order as `byGain` but presents `selectedIds`
chronologically, with unknown timestamps last and ids as the final tie-break
(`album-planner.ts:275-304`). `selectBestShots` uses `selectedIds`, not `byGain`
(`select-best-shots.ts:209-240`).

## 7. Output accounting and manual edits

Each selected take winner becomes exactly one `Selected` record and receives:

> `page: index + 1,`
>
> — `apps/mobile/src/selection/select-best-shots.ts:230-240`

Every other member of that same take is attached as an alternative, ranked by
the within-take comparator. The review bridge exposes only the first four
alternatives (`build-album.ts:523-535`).

Every analyzed candidate not selected becomes a pool item with a rounded quality
and textual reasons. Screenshots get a dedicated reason; other pool reasons are
qualitative (`select-best-shots.ts:242-263,551-652`). Photos rejected by the
64-photo prepass never enter `selectBestShots`, so they appear in neither the
pool nor alternatives and have no product-visible rejection record.

The review screen lets the user swap only within a selected take, remove a
selected slot, or add a pool photo (`review/ReviewScreen.tsx:78-162,177-263`).
These actions do not rerun the planner, its caps, or its coverage objective.
Finalization saves the resulting ids and numeric pages as-is
(`apps/mobile/App.tsx:353-383`).

## 8. What the current path does about the governing album-page rule

The current mobile result is a list of photographs with one sequential page
number each. There is no page placement object, mat/frame size, orientation
demand, hero/supporting/breather role, spread balance, full-bleed flag, bokeh
score, or gallery-wall constraint in `AlbumData` (`selection/types.ts:1-29`).
Width and height affect prepass resolution, final resolution quality, and
screenshot heuristics; they do not constrain the selected orientation mix.

The review screen itself renders square, two-column cards (`ReviewScreen.tsx:
305-315`), but that is review chrome, not an album-page plan. The final screen
shows only the first photo as a square cover (`review/FinalAlbum.tsx:63-65,
94-109`). Therefore the current selector neither produces a forbidden printed
grid nor produces the required gallery wall: it produces no composed album page
at all. It also contains no density or full-bleed bokeh-breather decision.

## 9. Reproduced failure, with executable evidence

The runnable evidence is `scratch/album-selection-explain.ts`. It imports and
runs the production `chooseHeavyAnalysisCandidates` and `selectBestShots`
functions (`scratch/album-selection-explain.ts:17-23`). Its explanatory replay
asserts exact candidate membership against the production prepass before
printing the trace (`scratch/album-selection-explain.ts:306-321`).

The fixture contains:

- 80 sharper variations of one posed portrait burst, all containing
  `person-main`;
- eight lower-contrast story beats—arrival, invitation detail, grandparent
  embrace, cake cutting, wide room, child candid, decor detail, and farewell—
  each containing a different face-cluster id;
- no reliable capture time, one place, and the same valid coarse BlurHash light
  layout for every photo.

The fixture is at `scratch/album-selection-explain.ts:81-145`. The trace copies
the production prepass arithmetic at lines 157-291. The deep-analysis fixture
gives the surviving burst frames pairwise cosine 0.91, just below both 0.92
take-collapse and selected-similarity thresholds
(`scratch/album-selection-explain.ts:294-300,351-379`).

Run it with Node 22:

```sh
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
node --experimental-strip-types \
  --experimental-loader ./scratch/ts-extension-loader.mjs \
  ./scratch/album-selection-explain.ts
```

Observed production-backed result:

```text
fixture=88 photos; prepass_limit=64; capture_time=missing;
place=single-session; content_key=identical

first burst frame:
quality=0.970 time=0.000 place=0.450 content=0.900 total=2.320

64th burst frame:
quality=0.970 time=0.000 place=0.002 content=0.002 total=0.974

each story frame after the cap fills:
quality=0.750 time=0.000 place=0.002 content=0.002 total=0.754

selected=24; posed-portrait-burst=24; story-beats=0;
story-beats-killed-at-prepass=8; rare-people-killed-before-planner=8
```

The gate chooses burst frames 1-64. Once one common place/content key has been
seen, both coverage bonuses rapidly approach zero and quality is the dominant
term. All eight story beats and all eight of their rare face-cluster ids are
dropped by `candidate_cap_64`; they are absent from the planner universe, so
`minPerPerson: 1`, rare-moment rescue, the semantic axes, pose diversity, and the
final ranker cannot recover them.

The 64 survivors then sit at cosine 0.91. That is below the 0.92 take threshold,
so the code calls each a “distinct visual take”; it is also below the planner's
0.92 preferred-distinct threshold. The production selector fills all 24 slots
with the same person's posed burst and reports “Adds another moment to the
story” for those synthetic frames. This is the demonstrated bad album: zero
opener, details, embrace, action, wide, candid, or closer, and no supply from
which a balanced gallery wall or bokeh breather could be composed.

## 10. Review-only fix proposal — do not ship before owner review

The failure is a shortlist-recall failure, not primarily a final-weight tuning
failure. Changing `weightMoment`, `weightPerson`, aesthetic weight, or the final
ranker cannot recover a photo that is not among the 64 analyzed candidates.

Proposed change:

1. Make the prepass an explicit constrained shortlist builder over evidence that
   already exists before heavy analysis. Preserve pins first, then seed the
   shortlist with max-coverage representatives for high-confidence
   `PickedPhoto.personIds`, prioritizing people with the fewest candidate photos.
   A photo covering multiple still-uncovered people can satisfy them together.
   The 64-photo safety limit remains hard.
2. Add a bounded per-bucket occupancy rule before quality fill. The bucket must
   combine available time, coarse content key, place, and face-cluster set, so a
   single burst cannot consume all remaining capacity merely because its frames
   are sharper. Relax occupancy deterministically only when the shortlist would
   otherwise underfill.
3. Fill the remaining capacity with the existing `Qcheap + coverage` objective.
   Do not alter final planner weights in the same change; that would make the
   cause of any improvement unmeasurable.
4. Pass a layout-demand summary into selection before a later layout stage:
   required hero/supporting/breather supply and orientation supply per gallery
   wall. A bokeh breather must be a positive role, not merely a low-aesthetic
   scene that survives by chance. Keep this contract separate from printed
   placement; the page composer still decides mixed frame sizes and balance.
5. Extend selection accounting across the boundary: every prepass drop records
   cap, score terms, bucket counts, and protected-coverage status. The product's
   current pool cannot explain candidates that never reached heavy analysis.

Measurement that would prove the gate fix:

- Turn the harness in section 9 into a regression gate. It must retain all eight
  rare-person/story representatives in the 64 candidates; the planner's person
  floor must then select all eight. On this exact 88-photo fixture that makes
  the pass/fail numbers 8/8 rare-person recall and at most 56/64 shortlist burst
  occupancy. The current result is 0/8 and 64/64.
- On a frozen, human-labeled sample from the 11,829-photo face-scanned library,
  measure **shortlist recall@64 before final ranking**. Report overall gold-pick
  recall, rare-person recall, story-role recall, and maximum occupancy by
  human-labeled burst. This is the primary success measure because the observed
  loss happens at the gate.
- Require no regression in hard exclusions, pin survival up to the documented
  64 limit, runtime, memory, or 24-slot fill rate.
- Only after shortlist recall passes, blind-pair the resulting albums. The
  acceptance test is whether reviewers prefer the new set for people coverage,
  moment/story coverage, and its ability to supply mixed-orientation matted
  gallery walls with deliberate full-bleed bokeh breathers. Track each dimension
  separately; a single aggregate preference number would not show whether this
  failure was actually fixed.
