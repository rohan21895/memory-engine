import type { FaceEmbeddingVector, FaceObservation, Person } from "./types";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { resolveConstraints, type AnchorBars, type FaceConstraint } from "./face-constraints.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { AGE_COEFFICIENTS, AGE_INTERCEPT, BABY_SCORE_CUT, babyScore } from "./face-age-prior.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { clusterFacesNatively } from "../../modules/photeo-scan-service/src/index.ts";

/**
 * Cosine bar for "same person" when assigning an aligned face to an existing
 * cluster.
 *
 * 0.5 was calibrated against UNALIGNED bounding-box crops, whose embeddings were
 * scattered enough that a loose bar was needed to group anyone at all. Once
 * 5-point alignment landed, every face moved much closer together and that same
 * bar collapsed the whole library into a single identity. Verification
 * thresholds for ArcFace-family embeddings sit near 0.3-0.45, but clustering
 * must be strictly tighter: assignment errors are transitive, so one bad link
 * merges two entire people.
 *
 * Now in w600k_mbf space (512-dim). Measured on 1,471 LFW crops through the
 * bundled TFLite build, this bar sits between an impostor p99 of 0.169 and a
 * genuine p05 of 0.423 — a real gap, where the old model's two distributions
 * overlapped.
 */
export const DEFAULT_IDENTITY_THRESHOLD = 0.39;

/**
 * Cluster-to-cluster merging is the transitive step, so it is tighter still.
 * A centroid is an average of many faces; two centroids being merely similar is
 * far weaker evidence than two faces being similar, and a single wrong merge is
 * unrecoverable for the user (two people permanently fused under one name).
 */
export const DEFAULT_MERGE_THRESHOLD = 0.6;

/**
 * Both clusters must hold at least this many faces to use the EVIDENCED merge
 * bar instead of this strict one.
 *
 * The case for a lower merge bar is that average linkage over n*m cross pairs
 * concentrates near the different-person mean rather than its tail, so a bar
 * inherited from single-pair statistics is far too strict. That argument
 * evaporates for a two-face cluster, whose "average" is one or two numbers and
 * whose tail behaves like a raw pair. Small clusters therefore keep the strict
 * bar, and only groups with real evidence behind them get the relaxed one.
 *
 * Lives here rather than in face-calibration.ts because that module already
 * imports this one; putting it there would close an import cycle.
 */
export const MERGE_EVIDENCE_MIN_FACES = 4;

/**
 * How much an ASSIGNABLE (lower-quality) face counts toward a centroid.
 *
 * A blurry, small or steeply-profiled face is still that person and still
 * belongs in their album, so it is assigned normally. But letting it move the
 * centroid as hard as a sharp frontal shot drags the cluster toward whatever
 * the bad frames have in common -- motion blur and profile geometry -- which is
 * a direction shared with OTHER people's bad frames. Contributing at a reduced
 * weight keeps the face and drops its vote.
 */
export const ASSIGNABLE_CENTROID_WEIGHT = 0.3;

/**
 * How far apart two clusters may sit in time and still be judged on the relaxed
 * temporal bar.
 *
 * An infant is the case no fixed threshold survives: a face at one month and
 * the same face at a year are barely related in embedding space, so no bar
 * joins them directly. Adjacent months DO match, and because merging runs
 * iteratively and closest-first, joining neighbours lets the union span a wider
 * window and reach the next neighbour -- the chain assembles itself without any
 * explicit path search.
 *
 * Sixty days is a compromise: wide enough that a gap in shooting does not break
 * the chain, narrow enough that two different people photographed the same
 * summer are not handed a discount for it.
 */
export const TEMPORAL_MERGE_WINDOW_MS = 60 * 24 * 60 * 60 * 1000;

export const DEFAULT_PERCEPTUAL_THRESHOLD = 0.92;

/**
 * Above this, two faces in ONE photo are the same face counted twice.
 *
 * Two things read it, and they agree because they are the same judgement:
 *   - `dedupeFaceObservations` deletes the repeat — ML Kit re-firing on one
 *     head, a mirror, a photo of a photo.
 *   - `samePhotoImpostorScores` refuses to count the pair as an impostor when
 *     calibrating a merge bar, since it is a genuine match wearing an impostor's
 *     label and sits exactly where the quantile is read.
 *
 * There was a THIRD reader until now: an escape that let two clusters merge
 * despite sharing a photo if they were similar enough. It is gone, and the
 * reason is worth recording, because it looked like a safety valve and was not.
 *
 * Measured on the owner's library, the escape had never once fired — 0 of 7,986
 * co-occurring cluster pairs reached 0.72, the highest being 0.6992. That is not
 * luck. `dedupeFaceObservations` runs FIRST and deletes every same-photo pair
 * above this same constant, so by the time clustering looked, no surviving pair
 * could possibly qualify. The escape was unreachable by construction.
 *
 * What it did do was carry risk: the case it protected (one person appearing
 * twice in a frame) is indistinguishable by similarity alone from the case it
 * endangered (two relatives who resemble each other), and in a family library
 * fusing a parent with their child is the one unrecoverable failure. So the
 * mirror case is handled where it can be handled — at detection, on box
 * geometry and identity together — and co-occurrence is now an absolute
 * cannot-link that only the user can overrule.
 *
 * Not separately measured, and it cannot be until there is a labelled corpus of
 * mirrors and collages. It fails safe in both remaining uses: too high keeps a
 * duplicate detection (the user sees one extra tile) and discards a few real
 * impostor pairs from a distribution built on thousands.
 */
export const SAME_PHOTO_DUPLICATE_SIMILARITY = 0.72;

/** True when two people draw on at least one common photo (a cannot-link). */
function sharesAsset(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  const [smaller, larger] = a.size <= b.size ? [a, b] : [b, a];
  for (const assetId of smaller) {
    if (larger.has(assetId)) return true;
  }
  return false;
}

/**
 * How many photos two clusters both appear in.
 *
 * Merging only needs to know WHETHER they co-occur, but a human being asked to
 * overrule that veto needs to know how much evidence is behind it -- ten shared
 * photos is a relationship, one out of five hundred faces is usually a double
 * detection.
 */
function countSharedAssets(
  a: ReadonlySet<string>,
  b: ReadonlySet<string>,
): number {
  const [smaller, larger] = a.size <= b.size ? [a, b] : [b, a];
  let shared = 0;
  for (const assetId of smaller) {
    if (larger.has(assetId)) shared += 1;
  }
  return shared;
}

/** True when the two sets have any element in common. */
function intersects(a: ReadonlySet<number>, b: ReadonlySet<number>): boolean {
  const [smaller, larger] = a.size <= b.size ? [a, b] : [b, a];
  for (const value of smaller) {
    if (larger.has(value)) return true;
  }
  return false;
}

/**
 * `k/127` for all 256 bytes an int8 embedding component can hold.
 *
 * This table is the whole reason storing embeddings as `Int8Array` is safe to do
 * to a library of somebody's family. The obvious int8 scheme — accumulate an
 * INTEGER dot product and apply `1/127^2` once at the end — is more accurate
 * than what ships today and still wrong to adopt, because it is not the SAME
 * arithmetic: the merge candidate queue settles exact ties by `pairKey` and the
 * sweep is greedy, so a one-bit disagreement can reorder a merge and change the
 * final grouping. `252a07b` paid for a second dot product on every surviving
 * pair to avoid exactly that, and a person wrongly FUSED is the one failure no
 * later pass can undo.
 *
 * So the bytes are expanded back to the identical doubles instead. Each entry is
 * computed by the same `component / 127` division `dequantizeEmbedding` ran per
 * component, and a `Float64Array` returns it bit-for-bit, so every value the
 * clusterer sees is the value it saw before this change. The table is 2 KB and
 * replaces 74 MB.
 */
const DEQUANTIZED_BYTE = Float64Array.from(
  { length: 256 },
  // Sign-extend the byte, because `Int8Array` holds -128..127 and the index is
  // taken with `& 0xff`.
  (_unused, byte) => ((byte << 24) >> 24) / 127,
);

/**
 * A `number[]` view of either embedding form, for the arithmetic to index.
 *
 * A `number[]` is returned AS IS rather than copied: it is already what the
 * caller wants and every caller treats it as read-only. An `Int8Array` is
 * expanded once, which is what makes the compact form free at the point of use —
 * assignment expands one face, compares it against every person, and drops it.
 */
export function dequantized(embedding: FaceEmbeddingVector): number[] {
  if (!(embedding instanceof Int8Array)) return embedding;
  // `Array.from` over the typed array, which is the same construction the old
  // `dequantizeEmbedding` used, and deliberately NOT `new Array(n)` filled by
  // index. The result is handed straight into the dot-product loop that runs 512
  // dimensions against every person in the library, and the index-filled version
  // measured ~40% slower on a full recluster of the owner's library -- identical
  // arithmetic, identical partition, worse array shape for the engine to
  // iterate. Built this way the recluster is back at parity with `number[]`.
  return Array.from(embedding, (component) => DEQUANTIZED_BYTE[component & 0xff]);
}

function magnitude(values: number[]): number {
  let squared = 0;
  for (const value of values) {
    squared += value * value;
  }
  return Number.isFinite(squared) ? Math.sqrt(squared) : 0;
}

/**
 * Unit-norms an embedding so a centroid is a mean of UNIT vectors.
 *
 * That invariant is what makes `centroidScale` below meaningful: the length of
 * a mean of unit vectors is a measurement of how tightly the cluster agrees
 * with itself. `face-index.ts` already normalizes at its own trust boundary,
 * so on the shipped path this is idempotent; it is repeated here because
 * `clusterFaces` is a public export and a caller handing in longer vectors
 * would otherwise make every cluster look artificially loose.
 */
function unitEmbedding(embedding: FaceEmbeddingVector): number[] {
  const values = dequantized(embedding);
  const length = magnitude(values);
  return length > Number.EPSILON && Math.abs(length - 1) > 1e-9
    ? values.map((value) => value / length)
    : values;
}

/**
 * |centroid|, the AVERAGE-LINKAGE correction, clamped to 1.
 *
 * A centroid is the arithmetic mean of its members' unit embeddings, so for a
 * unit-length face f:
 *
 *   dot(f, centroid) = mean_i cos(f, member_i)          <- average linkage
 *   cos(f, centroid) = mean_i cos(f, member_i) / |centroid|
 *
 * Cosine against a centroid therefore divides the honest quantity by a number
 * that SHRINKS as a cluster gets sloppier. That is a positive feedback loop and
 * it is the mechanism behind the one-tile library: every junk face a cluster
 * absorbs pulls its centroid toward the population mean, shortening it, which
 * RAISES its cosine against every remaining face, which qualifies the next junk
 * face. The biggest, worst cluster ends up the most attractive one, so it eats
 * the library and no threshold anywhere can stop it — the sweep at 0.62 through
 * 0.95 was re-measuring the same runaway at seven different speeds.
 *
 * Multiplying the cosine back by |centroid| recovers average linkage: the score
 * is the mean cosine between the candidate and every face already in the
 * cluster. It is bounded by the cluster's best member rather than inflated past
 * it, and absorbing a bad face now makes a cluster LESS attractive, which is the
 * direction that terminates. It also puts assignment, merging and the same-photo
 * exception back on the same face-to-face scale the thresholds were reasoned
 * about on.
 *
 * Clamped to 1 so it can only ever tighten: an over-long centroid (int8
 * dequantization noise, or a caller that built one by hand) must not be allowed
 * to manufacture similarity.
 */
function centroidScale(centroid: number[]): number {
  const length = magnitude(centroid);
  return length > Number.EPSILON ? Math.min(1, length) : 1;
}

/**
 * The per-vector half of a scaled similarity, computed ONCE per vector.
 *
 * Both places that compare vectors want `cosine(a,b)` multiplied by the scale
 * of each side, and that product collapses:
 *
 *   dot/(|a|*|b|) * min(1,|a|) * min(1,|b|)  ==  dot/(max(1,|a|) * max(1,|b|))
 *
 * because min(1,x)/x is exactly 1/max(1,x). So each vector contributes one
 * number, 1/max(1,|v|), and the comparison itself is a bare dot product.
 *
 * This is not a micro-optimisation. `cosine` recomputed BOTH norms on every
 * call, so a rebuild recomputed each face's norm once per person and each
 * centroid's norm once per face: three accumulators and two isFinite checks per
 * dimension where one multiply-add would do. Measured on a real library that
 * rebuild took 304 SECONDS over 7,937 faces and 1,010 people, freezing the app.
 *
 * Returns 0 for a vector that cannot be compared, which makes the similarity 0
 * exactly as `cosine`'s own non-finite and zero-norm guards did.
 */
export function comparisonInverse(values: number[]): number {
  let squared = 0;
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (!Number.isFinite(value)) return 0;
    squared += value * value;
  }
  if (!Number.isFinite(squared) || squared === 0) return 0;
  return 1 / Math.max(1, Math.sqrt(squared));
}

/**
 * Width of one early-exit block. 64 of 512 dimensions, so a hopeless comparison
 * is abandoned after an eighth of the work at best, and costs one extra multiply
 * and compare per block at worst.
 */
const BOUND_BLOCK = 64;

/**
 * Norms of each trailing slice of a vector, at `BOUND_BLOCK` boundaries.
 *
 * `suffix[k]` is the length of everything from dimension `k * BOUND_BLOCK`
 * onward, which by Cauchy-Schwarz bounds how much the rest of a dot product can
 * still contribute: |sum_{i>=P} a_i b_i| <= ||a_{>=P}|| * ||b_{>=P}||. That is
 * what lets `boundedSimilarity` stop early without ever changing an answer.
 */
function suffixNorms(values: number[]): Float64Array {
  const blocks = Math.ceil(values.length / BOUND_BLOCK);
  const suffix = new Float64Array(blocks + 1);
  let squared = 0;
  for (let block = blocks - 1; block >= 0; block -= 1) {
    const start = block * BOUND_BLOCK;
    const end = Math.min(start + BOUND_BLOCK, values.length);
    for (let index = start; index < end; index += 1) {
      squared += values[index] * values[index];
    }
    suffix[block] = Math.sqrt(squared);
  }
  return suffix;
}

/**
 * `scaledSimilarity`, but allowed to give up once the answer provably cannot
 * reach `required`. Returns NEGATIVE_INFINITY when it gave up.
 *
 * Assignment compares one face against every person and keeps the best match
 * over a bar, so the vast majority of those dot products exist only to be
 * discarded. This runs them a block at a time and, after each block, adds the
 * largest contribution the remaining dimensions could possibly make. If even
 * that optimistic total falls short, the remaining dimensions cannot change the
 * outcome and are skipped.
 *
 * The bound is Cauchy-Schwarz, so this is EXACT, not approximate: it returns the
 * same value as `scaledSimilarity` for every comparison that could have been
 * chosen, and abandons only ones that could not. `face-cluster-bound.test.ts`
 * asserts that equivalence directly, and the offline bench compares partition
 * hashes -- a faster pass that groups differently is not a fix.
 */
function boundedSimilarity(
  a: number[],
  aInverse: number,
  aSuffix: Float64Array,
  b: number[],
  bInverse: number,
  bSuffix: Float64Array,
  required: number,
): number {
  if (aInverse === 0 || bInverse === 0) return 0;
  if (a.length === 0 || a.length !== b.length) return 0;
  const scale = aInverse * bInverse;
  // Work in raw dot-product units so the bound needs no division per block.
  const requiredDot = required / scale;
  let dot = 0;
  let block = 0;
  for (let index = 0; index < a.length; ) {
    const end = Math.min(index + BOUND_BLOCK, a.length);
    for (; index < end; index += 1) {
      dot += a[index] * b[index];
    }
    block += 1;
    if (dot + aSuffix[block] * bSuffix[block] < requiredDot) {
      return Number.NEGATIVE_INFINITY;
    }
  }
  return dot * scale;
}

/**
 * Scaled similarity from two vectors and their precomputed inverses.
 *
 * The guards live here rather than in the loop so the hot path stays a single
 * multiply-add per dimension. A zero inverse means the vector already failed
 * validation, so it short-circuits before the dot product can produce NaN.
 */
export function scaledSimilarity(
  a: number[],
  aInverse: number,
  b: number[],
  bInverse: number,
): number {
  if (aInverse === 0 || bInverse === 0) return 0;
  if (a.length === 0 || a.length !== b.length) return 0;
  let dot = 0;
  for (let index = 0; index < a.length; index += 1) {
    dot += a[index] * b[index];
  }
  return dot * aInverse * bInverse;
}

export function cosine(a: number[], b: number[]): number {
  if (a.length === 0 || a.length !== b.length) {
    return 0;
  }

  let dot = 0;
  let normA = 0;
  let normB = 0;

  for (let index = 0; index < a.length; index += 1) {
    const aValue = a[index];
    const bValue = b[index];
    if (!Number.isFinite(aValue) || !Number.isFinite(bValue)) {
      return 0;
    }
    dot += aValue * bValue;
    normA += aValue * aValue;
    normB += bValue * bValue;
  }

  if (normA === 0 || normB === 0) {
    return 0;
  }

  return dot / Math.sqrt(normA * normB);
}

/**
 * Folds one face into a centroid as a WEIGHTED mean.
 *
 * `previousWeight` was a plain face count before quality weighting existed, and
 * an omitted `weight` still reproduces that exactly, so every existing caller
 * and test keeps its old behaviour.
 */
export function updateCentroid(
  centroid: number[],
  embedding: number[],
  previousWeight: number,
  weight = 1,
): number[] {
  if (
    centroid.length === 0 ||
    centroid.length !== embedding.length ||
    previousWeight <= 0 ||
    !(weight > 0)
  ) {
    return centroid.slice();
  }

  const total = previousWeight + weight;
  return centroid.map(
    (value, index) =>
      (value * previousWeight + embedding[index] * weight) / total,
  );
}

/**
 * A face's vote over its cluster's centroid.
 *
 * `seedable` is the quality tier the scanner already assigns -- a face good
 * enough to START a person. Anything below that is kept and assigned but only
 * partly trusted to steer the average.
 */
function centroidWeight(observation: FaceObservation): number {
  return observation.seedable === false ? ASSIGNABLE_CENTROID_WEIGHT : 1;
}

/**
 * A capture time fit to bound a span, or undefined when there is none.
 *
 * A non-positive time is NO time, not 1 January 1970. Android's DATE_TAKEN is
 * literally 0 for any file whose EXIF the media scanner could not read, and
 * `Number.isFinite(0)` is true, so the old guard admitted those as a real
 * instant: 73% of the owner's library landed on the epoch, every such cluster
 * then span-overlapped every other, and `spanGap` returned 0 for the pair. That
 * handed the relaxed temporal bar to essentially every evidenced pair in the
 * library, which is the opposite of a 60-day window.
 *
 * The guard lives HERE and not only at the scanner because observations already
 * written to disk keep their stored `capturedAt: 0` forever -- a processed asset
 * is never re-scanned, so a scanner-only fix would leave every existing install
 * clustering on the epoch. Every route a time can take into a span goes through
 * this one function: the assignment loop's newborn cluster, the widen, and the
 * rehydration of a stored `firstAt`/`lastAt` -- a stored index on disk already
 * holds 1,966 people at `firstAt: 0`, so the rehydration is not theoretical.
 */
function spanTime(capturedAt: number | undefined): number | undefined {
  return typeof capturedAt === "number" &&
    Number.isFinite(capturedAt) &&
    capturedAt > 0
    ? capturedAt
    : undefined;
}

/** Grows a cluster's capture-time span to include one more face. */
function widenSpan(person: MutablePerson, capturedAt: number | undefined): void {
  const at = spanTime(capturedAt);
  if (at === undefined) return;
  person.firstAt = person.firstAt === undefined ? at : Math.min(person.firstAt, at);
  person.lastAt = person.lastAt === undefined ? at : Math.max(person.lastAt, at);
}

/**
 * Gap in milliseconds between two clusters' capture spans, or undefined when
 * either has no time at all. Overlapping spans give 0.
 */
function spanGap(a: MutablePerson, b: MutablePerson): number | undefined {
  if (
    a.firstAt === undefined || a.lastAt === undefined ||
    b.firstAt === undefined || b.lastAt === undefined
  ) {
    return undefined;
  }
  if (a.lastAt >= b.firstAt && b.lastAt >= a.firstAt) return 0;
  return a.lastAt < b.firstAt ? b.firstAt - a.lastAt : a.firstAt - b.lastAt;
}

/**
 * Is this cluster a MOMENT rather than a lifetime?
 *
 * `spanGap` returns 0 for spans that merely OVERLAP, and a person photographed
 * across a family library spans the whole library -- so their span overlaps
 * everybody's and the "60-day window" opens for every pair they are in. That is
 * not the rule's intent. The discount exists because two clusters may be two
 * MOMENTS in one timeline, close enough that an infant's appearance has not yet
 * drifted between them; a cluster covering two years is not a moment, and
 * nothing about it says its faces sit near the other cluster's in time.
 *
 * Measured on the owner's library, without this the window is not a window: with
 * genuine capture times spread over two years, 70.9% of evidenced pairs are
 * still "near in time" (92.1% over six months), and the resulting partition is
 * byte-identical to the one the epoch-zero bug produced -- the same 2,248
 * tiles, the same ten merges, 301+159 faces among them. With it, the same
 * genuine times produce 2,258 and refuse all ten.
 */
function narrowSpan(person: MutablePerson): boolean {
  return (
    person.firstAt !== undefined &&
    person.lastAt !== undefined &&
    person.lastAt - person.firstAt <= TEMPORAL_MERGE_WINDOW_MS
  );
}

type MutablePerson = Person & {
  assetIdSet: Set<string>;
  /** 1/max(1,|centroid|): the centroid's half of a scaled similarity. */
  inverse: number;
  /** Trailing-slice norms of `centroid`, for the assignment loop's early exit. */
  suffix: Float64Array;
  /** Sum of quality weights behind `centroid`, which is a weighted mean. */
  weightSum: number;
  /** Capture-time span of this cluster; undefined when no face carried a time. */
  firstAt?: number;
  lastAt?: number;
};

/**
 * Refreshes both cached magnitudes of a moved centroid.
 *
 * Always together, and only here. `inverse` and `suffix` are two views of the
 * same vector's length, and updating one without the other leaves the
 * assignment loop pruning against a centroid that no longer exists. That
 * failure is silent -- a stale suffix is usually LARGER than the true one,
 * which merely wastes work, so it survives a test suite and then bites on the
 * one library where it is smaller and a real match gets abandoned. Construction
 * sites are already safe: `suffix` is a required field, so a literal that omits
 * it does not compile.
 */
function refreshCentroidMagnitudes(person: MutablePerson): void {
  person.inverse = comparisonInverse(person.centroid);
  person.suffix = suffixNorms(person.centroid);
}

/** Restores the merge-only state hidden behind the public `Person` shape. */
function mutablePerson(person: Person): MutablePerson {
  const stored = person as Person & {
    firstAt?: unknown;
    lastAt?: unknown;
    weightSum?: unknown;
  };
  return {
    ...person,
    assetIds: person.assetIds.slice(),
    centroid: person.centroid.slice(),
    assetIdSet: new Set(person.assetIds),
    inverse: comparisonInverse(person.centroid),
    suffix: suffixNorms(person.centroid),
    // New clusters retain their quality-weighted sum in memory and on disk.
    // Legacy records do not have it, so faceCount reproduces their historical
    // unweighted centroid semantics exactly.
    weightSum:
      typeof stored.weightSum === "number" &&
      Number.isFinite(stored.weightSum) &&
      stored.weightSum > 0
        ? stored.weightSum
        : person.faceCount,
    firstAt: spanTime(
      typeof stored.firstAt === "number" ? stored.firstAt : undefined,
    ),
    lastAt: spanTime(
      typeof stored.lastAt === "number" ? stored.lastAt : undefined,
    ),
  };
}

function publicPerson(person: MutablePerson): Person {
  const {
    assetIdSet: _assetIdSet,
    inverse: _inverse,
    suffix: _suffix,
    ...record
  } = person;
  return record;
}

type ClusterOptions = {
  /** User judgements about who is who. They outrank every measured bar. */
  constraints?: readonly FaceConstraint[];
  identityMergeThreshold?: number;
  /** Merge bar for clusters within TEMPORAL_MERGE_WINDOW_MS of each other. */
  temporalMergeThreshold?: number;
  /**
   * Merge bar for two clusters that BOTH clear MERGE_EVIDENCE_MIN_FACES.
   *
   * Unlike `identityMergeThreshold` this is deliberately NOT raised to the
   * assignment bar. Assignment weighs one face against a group; this weighs two
   * groups against each other, over n*m cross pairs, and that average follows
   * the different-person MEAN rather than its tail. Being looser than
   * assignment is therefore correct here, not the bug the clamp below guards
   * against -- but only once both sides have enough faces for the average to
   * mean anything, which is what the size gate enforces.
   */
  evidencedMergeThreshold?: number;
  onAssign?: (observation: FaceObservation, personId: string) => void;
  onMerge?: (absorbedPersonId: string, survivingPersonId: string) => void;
  /** Fires only when consolidation actually starts, after `skipMerge`. */
  onMergeSweep?: (path: "full" | "restricted") => void;
  threshold?: number;
  perceptualThreshold?: number;
  /**
   * Assign the new faces but skip cluster-to-cluster consolidation.
   *
   * Merging is O(people^2) TWICE over -- once to build the same-photo
   * cannot-link sets, once for the closest-first sweep -- and it does not care
   * whether 40 faces arrived or 40,000. Measured mid-scan on a real library it
   * cost 17 SECONDS per 32-photo batch at 1,235 people, more than detection and
   * embedding put together, and it grows quadratically for the rest of the scan.
   *
   * Assignment is exact either way: a face still joins the best person it
   * clears the bar against. Only the consolidation of two clusters into one is
   * deferred, so the cost is transient -- a person may show as two tiles until
   * the next consolidation, and the full rebuild at scan end produces exactly
   * the grouping it always did. Skipping merges can never FUSE two people, only
   * leave them split a while longer, which is the failure direction this
   * codebase already prefers.
   */
  skipMerge?: boolean;
  /**
   * Person ids whose clusters changed since the last completed consolidation.
   *
   * When present, the initial merge sweep only has to seed pairs touching one
   * of these people. Every other pair was already rejected at the same bars
   * with the same endpoints. A merge keeps its survivor active and refreshes
   * that survivor against every remaining person, so indirect changes still
   * propagate to the same fixed point as the full sweep.
   *
   * Omit for a full sweep. An empty set deliberately visits no measured pair.
   */
  mergeSeedPersonIds?: ReadonlySet<string>;
};

/**
 * Greedy online clustering against each existing centroid.
 *
 * This is O(n * k * d), where k is the number of people and d is the embedding
 * size. Brute-force centroid search is appropriate for tens of thousands of
 * faces; beyond that ceiling this should be replaced by an approximate index.
 */
export function clusterFaces(
  observations: FaceObservation[],
  opts: ClusterOptions = {},
): Person[] {
  return extendFaceClusters([], observations, opts);
}

/**
 * Adds one resumable scan batch to existing clusters without waiting for the
 * complete photo library. Existing ids remain stable and all inputs are copied.
 */
/**
 * Every bar in one place, derived once.
 *
 * Extracted so `suggestMerges` cannot answer "why did this pair not merge?"
 * with a different set of numbers than the merge pass actually used. A second
 * copy of this arithmetic would drift, and the symptom would be a suggestion
 * list that disagrees with the grouping it is meant to explain.
 */
function mergeBars(opts: ClusterOptions): {
  identity: number;
  perceptual: number;
  evidenced: number;
  temporal: number;
  assignment: number;
} {
  const assignment = Number.isFinite(opts.threshold)
    ? (opts.threshold as number)
    : DEFAULT_IDENTITY_THRESHOLD;
  const perceptual = Number.isFinite(opts.perceptualThreshold)
    ? (opts.perceptualThreshold as number)
    : DEFAULT_PERCEPTUAL_THRESHOLD;
  // Merging must never be looser than assignment: if two faces were too far
  // apart to join a cluster, two averaged centroids at that distance are weaker
  // evidence still. The clamp is UNCONDITIONAL, not a fallback for callers who
  // supply nothing. It used to guard only the default branch, so an explicit
  // `identityMergeThreshold: 0.37` from face-index.ts sailed straight past it
  // and the whole recalibration to 0.72 was silently inert on the only path
  // that ships. A caller asking for a looser bar than assignment is asking for
  // a bug, so raise it rather than obey it.
  const identity = Math.max(
    assignment,
    Number.isFinite(opts.identityMergeThreshold)
      ? (opts.identityMergeThreshold as number)
      : DEFAULT_MERGE_THRESHOLD,
  );
  // Not clamped to the assignment bar -- see `evidencedMergeThreshold` above.
  // Defaults to the strict bar, so a caller that says nothing gets exactly
  // today's behaviour and this stays inert until someone opts in.
  const evidenced = Math.min(
    identity,
    Number.isFinite(opts.evidencedMergeThreshold)
      ? (opts.evidencedMergeThreshold as number)
      : identity,
  );
  // Also uncapped by the assignment bar, for the same reason as the evidenced
  // bar: it governs two averages, not a face against a group.
  const temporal = Math.min(
    evidenced,
    Number.isFinite(opts.temporalMergeThreshold)
      ? (opts.temporalMergeThreshold as number)
      : evidenced,
  );
  return { identity, perceptual, evidenced, temporal, assignment };
}

export function extendFaceClusters(
  existing: Person[],
  observations: FaceObservation[],
  opts: ClusterOptions = {},
): Person[] {
  const bars = mergeBars(opts);
  const identityThreshold = bars.assignment;
  const perceptualThreshold = bars.perceptual;
  const identityMergeThreshold = bars.identity;
  const evidencedMergeThreshold = bars.evidenced;
  const temporalMergeThreshold = bars.temporal;
  const people: MutablePerson[] = existing.map(mutablePerson);
  let nextPersonNumber = people.reduce((largest, person) => {
    const match = /^person-(\d+)$/u.exec(person.id);
    return match ? Math.max(largest, Number(match[1])) : largest;
  }, 0) + 1;

  for (const observation of observations) {
    const embedding = unitEmbedding(observation.embedding);
    // Hoisted deliberately. This face is compared against EVERY person, and the
    // old code recomputed its norm inside each of those comparisons: on the
    // measured library that was 1,010 redundant 512-dimension passes per face.
    const embeddingInverse = comparisonInverse(embedding);
    const embeddingSuffix = suffixNorms(embedding);
    // Fixed for this face: a person only reaches the comparison when its kind
    // already matches, so this cannot vary within the loop below.
    const threshold =
      observation.embeddingKind === "identity"
        ? identityThreshold
        : perceptualThreshold;
    let bestIndex = -1;
    let bestSimilarity = Number.NEGATIVE_INFINITY;

    for (let index = 0; index < people.length; index += 1) {
      const person = people[index];
      if (
        observation.embeddingKind !== person.embeddingKind ||
        embedding.length === 0 ||
        embedding.length !== person.centroid.length
      ) {
        continue;
      }

      // A person can only win by clearing the bar AND beating the best match so
      // far, so the bar rises as the loop finds better candidates and later
      // comparisons get cheaper. Everything below this cannot be chosen, which
      // is what makes abandoning it early lossless.
      const required =
        bestSimilarity > threshold ? bestSimilarity : threshold;
      // Cannot-link, with no way across it: this person already owns a face from
      // this very photo, so joining would fuse two people who merely posed
      // together. Checked BEFORE the dot product rather than after — the answer
      // cannot depend on the similarity any more, so computing it is wasted work
      // on exactly the comparisons a group photo makes most often.
      if (person.assetIdSet.has(observation.assetId)) continue;
      // Average linkage: the mean cosine between this face and every face
      // already in the person, NOT the cosine against their mean. See
      // `centroidScale`.
      const similarity = boundedSimilarity(
        embedding,
        embeddingInverse,
        embeddingSuffix,
        person.centroid,
        person.inverse,
        person.suffix,
        required,
      );
      if (similarity === Number.NEGATIVE_INFINITY) continue;
      if (similarity >= threshold && similarity > bestSimilarity) {
        bestIndex = index;
        bestSimilarity = similarity;
      }
    }

    if (bestIndex === -1) {
      if (observation.seedable === false) {
        continue;
      }
      const id = `person-${nextPersonNumber++}`;
      people.push({
        id,
        faceCount: 1,
        assetIds: [observation.assetId],
        centroid: embedding.slice(),
        embeddingKind: observation.embeddingKind,
        assetIdSet: new Set([observation.assetId]),
        inverse: comparisonInverse(embedding),
        // The centroid IS this embedding at birth, so its suffix norms are the
        // ones already computed above. Shared rather than recomputed because
        // neither array is ever mutated in place -- `suffix` is reassigned
        // wholesale whenever the centroid moves.
        suffix: embeddingSuffix,
        weightSum: centroidWeight(observation),
        firstAt: spanTime(observation.capturedAt),
        lastAt: spanTime(observation.capturedAt),
      });
      opts.onAssign?.(observation, id);
      continue;
    }

    const person = people[bestIndex];
    const weight = centroidWeight(observation);
    person.centroid = updateCentroid(
      person.centroid,
      embedding,
      person.weightSum,
      weight,
    );
    refreshCentroidMagnitudes(person);
    person.weightSum += weight;
    person.faceCount += 1;
    widenSpan(person, observation.capturedAt);
    if (!person.assetIdSet.has(observation.assetId)) {
      person.assetIdSet.add(observation.assetId);
      person.assetIds.push(observation.assetId);
    }
    opts.onAssign?.(observation, person.id);
  }

  if (!opts.skipMerge) {
    opts.onMergeSweep?.(
      opts.mergeSeedPersonIds === undefined ? "full" : "restricted",
    );
    mergeSimilarPeople(
      people,
      identityMergeThreshold,
      perceptualThreshold,
      evidencedMergeThreshold,
      temporalMergeThreshold,
      opts.constraints ?? [],
      bars,
      opts.mergeSeedPersonIds,
      opts.onMerge,
    );
  }

  return people.map(publicPerson);
}

/**
 * Directly merges two people that already exist in the current index.
 *
 * This deliberately routes through `absorb`, the same operation used by both
 * measured and constraint-forced clustering merges. It is O(embedding dims +
 * absorbed assets), independent of the number of observations and people in
 * the library. The older array position survives, matching the full merge pass.
 */
export function mergeExistingPeople(
  people: Person[],
  firstIndex: number,
  secondIndex: number,
  onMerge?: (absorbedPersonId: string, survivingPersonId: string) => void,
): boolean {
  if (
    !Number.isInteger(firstIndex) ||
    !Number.isInteger(secondIndex) ||
    firstIndex < 0 ||
    secondIndex < 0 ||
    firstIndex >= people.length ||
    secondIndex >= people.length ||
    firstIndex === secondIndex
  ) {
    return false;
  }
  const keepIndex = Math.min(firstIndex, secondIndex);
  const dropIndex = Math.max(firstIndex, secondIndex);
  const survivor = people[keepIndex];
  const absorbed = people[dropIndex];
  if (
    survivor.embeddingKind !== absorbed.embeddingKind ||
    survivor.centroid.length === 0 ||
    survivor.centroid.length !== absorbed.centroid.length
  ) {
    return false;
  }

  // A two-element working set lets the existing absorb operation carry every
  // merge semantic without allocating structures proportional to all people.
  const pair = [mutablePerson(survivor), mutablePerson(absorbed)];
  absorb(
    pair,
    [new Set([0]), new Set([1])],
    [new Set<number>(), new Set<number>()],
    0,
    1,
    onMerge,
  );
  people[keepIndex] = publicPerson(pair[0]);
  people.splice(dropIndex, 1);
  return true;
}

/**
 * Second pass: greedy online assignment is order-dependent, so one person's
 * faces routinely seed several clusters (a bad-angle first frame the later good
 * frames never match). Repeatedly merge the CLOSEST pair of same-kind people
 * above a calibrated centroid bar — closest-first, not first-found, so the
 * outcome does not depend on the order people were discovered in. Co-occurrence
 * stays a cannot-link below SAME_PHOTO_DUPLICATE_SIMILARITY, and the merged
 * asset set inherits both constraints, so identities cannot be chained through
 * a bridge cluster. Every round removes exactly one person, so the loop is
 * bounded by the initial people count. The older cluster (lower index) survives,
 * keeping surfaced ids stable between runs so the UI does not reshuffle.
 *
 * The cannot-link is FROZEN before the first merge, because a live re-check is
 * defeated by exactly the chain it is supposed to stop. It used to re-evaluate
 * `similarity >= SAME_PHOTO_DUPLICATE_SIMILARITY` against the current centroids,
 * so absorbing an unrelated bridge cluster could walk a centroid far enough to
 * clear the exception and void a constraint that held moments earlier:
 *
 *   ana    at   0 deg, photos {group-shot, ana-solo}
 *   bridge at  16 deg, photos {bridge-solo}
 *   cal    at  38 deg, photos {group-shot, cal-solo}
 *
 * ana and cal share group-shot at cosine 0.788, below the exception, so they are
 * a cannot-link and were never eligible to merge directly. But ana absorbs
 * bridge (0.961), which drags ana's centroid to 8 deg — and from there cal sits
 * at cosine 0.866, over the 0.85 exception, so the pair that was forbidden gets
 * merged on the very next round. Two faces from one photo, one tile. Freezing
 * the relation up front and inheriting it through `origins`/`blocked` makes the
 * constraint genuinely transitive: a cluster carries every cannot-link of every
 * cluster it is built from, and no amount of centroid drift can dissolve one.
 * (It is also cheaper — the asset-set intersection now runs once per pair
 * instead of once per pair per round.)
 *
 * ONE threshold governs, deliberately. There was a second, looser bar that took
 * over once both clusters held at least N faces, on the theory that a
 * well-supported centroid is a more trustworthy estimate. It is a
 * runaway-absorption engine: each merge pulls a centroid toward the population
 * mean, which RAISES its cosine against every cluster it has not eaten yet,
 * which qualifies the next merge, and the reward for having absorbed 10 faces is
 * a cheaper 11th. Measured in face-cluster-recovery.test.ts, that path alone
 * takes eight cleanly separated identities to a single 112-face blob even when
 * the base bar is correct. If large clusters ever need their own rule it must
 * demand MORE evidence, never less.
 *
 * Candidate similarities are scanned once, then only the survivor's row is
 * refreshed after each merge. No other pair can have changed its linkage,
 * threshold inputs, or inherited cannot-links.
 */
function mergeSimilarPeople(
  people: MutablePerson[],
  identityMergeThreshold: number,
  perceptualThreshold: number,
  evidencedMergeThreshold: number,
  temporalMergeThreshold: number,
  constraints: readonly FaceConstraint[],
  /** Bars a face-anchored constraint is resolved against; see `AnchorBars`. */
  bars: AnchorBars,
  /** Undefined means the historical full sweep. */
  mergeSeedPersonIds?: ReadonlySet<string>,
  onMerge?: (absorbedPersonId: string, survivingPersonId: string) => void,
): void {
  const comparable = (a: MutablePerson, b: MutablePerson): boolean =>
    a.embeddingKind === b.embeddingKind &&
    a.centroid.length > 0 &&
    a.centroid.length === b.centroid.length;
  /**
   * Mean cosine between every face of `a` and every face of `b`, abandoned as
   * soon as it cannot reach `required`.
   *
   * The sweep below is O(people^2) -- on the owner's library 2,244 people is
   * 2.5M pairs -- and the line after every call throws the answer away unless
   * it clears the bar. Nearly all of them do not: two people picked at random
   * out of a face library are close to orthogonal, so the running dot product
   * plus its own best case falls under the bar within a block or two, long
   * before 512 dimensions have been touched.
   *
   * The bound is used as a FILTER and nothing else, which is the only reason
   * this is safe to do to a library of somebody's family. `boundedSimilarity`
   * scales by `aInverse * bInverse` where `scaledSimilarity` scales by
   * `aInverse` then `bInverse`, and floating-point multiplication is not
   * associative, so the two can disagree in the last bit. That is normally
   * beneath notice, but the candidate queue settles EXACT ties by `pairKey`,
   * so a one-bit disagreement can turn a tie into an ordering -- and the sweep
   * is greedy, so merge order can change the final grouping. Recomputing the
   * survivors with the original expression keeps every value that reaches the
   * queue exactly the one that reached it before this change.
   *
   * Paying for the dot product twice on survivors is free in practice for the
   * same reason the filter works at all: almost nothing survives.
   *
   * `suffix` costs nothing to use here. It exists for the assignment loop and
   * `refreshCentroidMagnitudes` already restores it on every centroid move,
   * including the one inside `absorb` -- so the sweep cannot read a stale bound
   * even midway through a chain of merges.
   */
  const linkage = (
    a: MutablePerson,
    b: MutablePerson,
    required: number,
  ): number => {
    const reachable = boundedSimilarity(
      a.centroid,
      a.inverse,
      a.suffix,
      b.centroid,
      b.inverse,
      b.suffix,
      required,
    );
    if (reachable === Number.NEGATIVE_INFINITY) return Number.NEGATIVE_INFINITY;
    return scaledSimilarity(a.centroid, a.inverse, b.centroid, b.inverse);
  };
  /** Order-free name for a pair, used only to settle exact ties. */
  const pairKey = (a: MutablePerson, b: MutablePerson): string =>
    a.id < b.id ? `${a.id} ${b.id}` : `${b.id} ${a.id}`;

  // Cannot-link, frozen against the pre-merge clusters. `origins` tracks which
  // of those original clusters each surviving person is built from; `blocked`
  // tracks which of them it may never be joined to. Both sets union on a merge,
  // so the constraint is inherited rather than re-derived from a centroid that
  // has since moved. Applies to the perceptual fallback too: two faces in one
  // frame are two people whichever space measured them.
  const origins = people.map((_person, index) => new Set([index]));
  const blocked = people.map(() => new Set<number>());
  const active =
    mergeSeedPersonIds === undefined
      ? undefined
      : new Set(
          people
            .filter((person) => mergeSeedPersonIds.has(person.id))
            .map((person) => person.id),
        );
  const forEachSeedPair = (visit: (i: number, j: number) => void): void => {
    if (!active) {
      for (let i = 0; i < people.length; i += 1) {
        for (let j = i + 1; j < people.length; j += 1) visit(i, j);
      }
      return;
    }
    // Enumerate active rows, not the whole upper triangle with a cheap guard:
    // the latter still visits O(people^2) pairs and is exactly the phone cost
    // this path exists to remove. The active-active condition avoids duplicates.
    for (let i = 0; i < people.length; i += 1) {
      if (!active.has(people[i].id)) continue;
      for (let j = 0; j < people.length; j += 1) {
        if (i === j) continue;
        if (active.has(people[j].id) && j < i) continue;
        visit(Math.min(i, j), Math.max(i, j));
      }
    }
  };
  forEachSeedPair((i, j) => {
    const a = people[i];
    const b = people[j];
    if (!comparable(a, b)) return;
    if (!sharesAsset(a.assetIdSet, b.assetIdSet)) return;
    blocked[i].add(j);
    blocked[j].add(i);
  });

  // The user's own judgements, layered on top of the measured ones. A "not the
  // same person" joins the same `blocked` structure the same-photo rule uses,
  // so it inherits through merges for free. A "same person" is applied as a
  // forced merge below, before any similarity is consulted at all — the whole
  // point is that it holds where the numbers disagree.
  const resolved = resolveConstraints(people, constraints, bars);
  for (const [i, j] of resolved.cannot) {
    blocked[i].add(j);
    blocked[j].add(i);
  }
  // Every forced pair is translated to IDs before the first merge runs, because
  // `absorb` splices `people` and every later index would otherwise address a
  // different person than the one the constraint named. Positions are re-found
  // per pair; ids are stable, positions are not.
  const forced = resolved.must.map(
    ([ai, bi]) => [people[ai]?.id, people[bi]?.id] as const,
  );
  for (const [aId, bId] of forced) {
    if (!aId || !bId) continue;
    const i = people.findIndex((person) => person.id === aId);
    const j = people.findIndex((person) => person.id === bId);
    // A missing id means an earlier forced merge already absorbed it, which is
    // the transitive case (A=B and B=C makes A, B and C one person) and needs
    // no special handling beyond not treating it as an error.
    if (i === -1 || j === -1 || i === j) continue;
    if (!comparable(people[i], people[j])) continue;
    const [keep, drop] = i < j ? [i, j] : [j, i];
    const survivorId = people[keep].id;
    absorb(people, origins, blocked, keep, drop, onMerge);
    // A forced merge changes the survivor even when neither endpoint arrived
    // in this scan. Treat it as touched so its new centroid, size, time span,
    // and inherited cannot-links are compared with every remaining person.
    active?.add(survivorId);
  }

  type MergeCandidate = {
    similarity: number;
    pairKey: string;
    aId: string;
    bId: string;
    aVersion: number;
    bVersion: number;
  };
  const candidates: MergeCandidate[] = [];
  const versions = new Map(people.map((person) => [person.id, 0]));
  const indexById = new Map(people.map((person, index) => [person.id, index]));
  const betterCandidate = (a: MergeCandidate, b: MergeCandidate): boolean =>
    a.similarity > b.similarity ||
    (a.similarity === b.similarity && a.pairKey < b.pairKey);
  const pushCandidate = (candidate: MergeCandidate): void => {
    let index = candidates.push(candidate) - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (!betterCandidate(candidates[index], candidates[parent])) break;
      [candidates[index], candidates[parent]] = [
        candidates[parent],
        candidates[index],
      ];
      index = parent;
    }
  };
  const popCandidate = (): MergeCandidate | undefined => {
    const best = candidates[0];
    const tail = candidates.pop();
    if (candidates.length > 0 && tail) {
      candidates[0] = tail;
      let index = 0;
      for (;;) {
        const left = index * 2 + 1;
        const right = left + 1;
        let child = index;
        if (
          left < candidates.length &&
          betterCandidate(candidates[left], candidates[child])
        ) {
          child = left;
        }
        if (
          right < candidates.length &&
          betterCandidate(candidates[right], candidates[child])
        ) {
          child = right;
        }
        if (child === index) break;
        [candidates[index], candidates[child]] = [
          candidates[child],
          candidates[index],
        ];
        index = child;
      }
    }
    return best;
  };
  const scanPair = (i: number, j: number): void => {
    const a = people[i];
    const b = people[j];
    if (!comparable(a, b)) return;
    // Two clusters that each carry real evidence are judged on the bar
    // measured for AVERAGES; anything smaller is judged on the strict bar,
    // which is the one that behaves like a single pair.
    const evidenced =
      a.faceCount >= MERGE_EVIDENCE_MIN_FACES &&
      b.faceCount >= MERGE_EVIDENCE_MIN_FACES;
    // Two clusters that are each a MOMENT, and whose moments overlap or nearly
    // touch, get the temporal bar -- which is how an infant's months chain
    // together: each neighbouring pair clears it, the union spans wider, and
    // the next neighbour comes into range on the following iteration. Requires
    // BOTH to carry real evidence, so a stray two-face cluster cannot ride a
    // date into somebody else, and BOTH to be narrow, so a cluster spanning the
    // whole library cannot hand its overlap to everyone (see `narrowSpan`).
    const gap = spanGap(a, b);
    const nearInTime =
      gap !== undefined &&
      gap <= TEMPORAL_MERGE_WINDOW_MS &&
      narrowSpan(a) &&
      narrowSpan(b);
    const identityBar = evidenced
      ? nearInTime
        ? Math.min(evidencedMergeThreshold, temporalMergeThreshold)
        : evidencedMergeThreshold
      : identityMergeThreshold;
    const threshold =
      a.embeddingKind === "identity" ? identityBar : perceptualThreshold;
    // Same-photo cannot-links skipped by the restricted pre-pass are recovered
    // exactly when their pair becomes reachable. Since absorbed asset sets are
    // unions, a block can never disappear while a chain of merges proceeds.
    if (active && sharesAsset(a.assetIdSet, b.assetIdSet)) return;
    // `blocked` must be read on every survivor-row refresh because an absorb
    // unions both endpoints' inherited cannot-links.
    if (intersects(blocked[i], origins[j])) return;
    // Passing the bar in is what makes the call cheap: `linkage` may abandon
    // the dot product the moment it cannot reach the very bar tested next.
    const similarity = linkage(a, b, threshold);
    if (similarity < threshold) return;
    pushCandidate({
      similarity,
      pairKey: pairKey(a, b),
      aId: a.id,
      bId: b.id,
      aVersion: versions.get(a.id) ?? 0,
      bVersion: versions.get(b.id) ?? 0,
    });
  };

  // The forced-merge pass has completed before this one full similarity sweep.
  forEachSeedPair(scanPair);

  for (;;) {
    const candidate = popCandidate();
    if (!candidate) return;
    const aIndex = indexById.get(candidate.aId);
    const bIndex = indexById.get(candidate.bId);
    // An endpoint may have been absorbed while the candidate waited. A
    // survivor may also have changed; its old row entries are invalidated by
    // the version and replaced by the row refresh below.
    if (
      aIndex === undefined ||
      bIndex === undefined ||
      versions.get(candidate.aId) !== candidate.aVersion ||
      versions.get(candidate.bId) !== candidate.bVersion
    ) {
      continue;
    }

    const [keepIndex, dropIndex] =
      aIndex < bIndex ? [aIndex, bIndex] : [bIndex, aIndex];
    const survivorId = people[keepIndex].id;
    const absorbedId = people[dropIndex].id;
    absorb(people, origins, blocked, keepIndex, dropIndex, onMerge);
    active?.delete(absorbedId);
    active?.add(survivorId);

    indexById.delete(absorbedId);
    versions.delete(absorbedId);
    versions.set(survivorId, (versions.get(survivorId) ?? 0) + 1);
    for (let index = dropIndex; index < people.length; index += 1) {
      indexById.set(people[index].id, index);
    }

    // Only pairs touching the survivor can have changed their similarity,
    // evidence/temporal bars, or inherited cannot-links.
    for (let index = 0; index < people.length; index += 1) {
      if (index === keepIndex) continue;
      scanPair(Math.min(index, keepIndex), Math.max(index, keepIndex));
    }
  }
}

/**
 * Folds `dropIndex` into `keepIndex`, in place.
 *
 * Shared by measured merges and user-forced ones so a constraint cannot drift
 * from the normal path: the centroid is a weightSum-weighted mean of the two,
 * and both the origin and cannot-link sets union, which is what makes a
 * constraint inherit through later merges instead of being re-derived from a
 * centroid that has since moved.
 */
function absorb(
  people: MutablePerson[],
  origins: Array<Set<number>>,
  blocked: Array<Set<number>>,
  keepIndex: number,
  dropIndex: number,
  onMerge?: (absorbedPersonId: string, survivingPersonId: string) => void,
): void {
  const survivor = people[keepIndex];
  const absorbed = people[dropIndex];
  // Blended by WEIGHT, not face count: both centroids are weighted means, so
  // combining them on counts would quietly restore full influence to the
  // low-quality faces that were deliberately discounted on the way in.
  const totalWeight = survivor.weightSum + absorbed.weightSum;
  survivor.centroid = survivor.centroid.map(
    (value, index) =>
      (value * survivor.weightSum + absorbed.centroid[index] * absorbed.weightSum) /
      totalWeight,
  );
  refreshCentroidMagnitudes(survivor);
  survivor.faceCount = survivor.faceCount + absorbed.faceCount;
  survivor.weightSum += absorbed.weightSum;
  widenSpan(survivor, absorbed.firstAt);
  widenSpan(survivor, absorbed.lastAt);
  for (const assetId of absorbed.assetIds) {
    if (!survivor.assetIdSet.has(assetId)) {
      survivor.assetIdSet.add(assetId);
      survivor.assetIds.push(assetId);
    }
  }
  // The absorbed person's face is still a face of the same human, so it is
  // worth inheriting -- but only into an empty slot. Overwriting the
  // survivor's would replace a face the user has already learned to recognise
  // with a different one, for no gain.
  if (!survivor.avatarUri && absorbed.avatarUri) {
    survivor.avatarUri = absorbed.avatarUri;
    survivor.avatarAssetId = absorbed.avatarAssetId;
  }
  for (const origin of origins[dropIndex]) origins[keepIndex].add(origin);
  for (const origin of blocked[dropIndex]) blocked[keepIndex].add(origin);
  onMerge?.(absorbed.id, survivor.id);
  people.splice(dropIndex, 1);
  origins.splice(dropIndex, 1);
  blocked.splice(dropIndex, 1);
}

export type MergeSuggestion = {
  /** The larger cluster, so the UI can lead with the more recognisable face. */
  a: string;
  b: string;
  /** Mean cosine between the two clusters: the same number merging judges on. */
  similarity: number;
  /** The bar this pair actually failed, so the gap can be shown honestly. */
  bar: number;
  /**
   * Photos both clusters appear in.
   *
   * Shown because it is the evidence the user is being asked to overrule, and
   * its weight varies enormously: one shared photo out of five hundred is not
   * the same fact as ten shared photos out of twelve. The rate is context for a
   * human answer, never authority to merge the pair automatically.
   */
  sharedAssets: number;
  /**
   * Photos the smaller cluster appears in at all — the denominator that makes
   * `sharedAssets` mean something.
   *
   * One shared photo is a rate, but the current labelled set cannot say what a
   * low rate predicts. The 2026-08-29 audit found 89 persisted answers, 73 that
   * still resolve to identities, and only ONE current co-occurring pair; it was
   * labelled different people at 2.7%. One block is not a population. Keep the
   * denominator visible so the user can judge it, without turning it into a
   * probability claim the labels do not support.
   */
  appearances: number;
  /**
   * Photos this answer would put right: the size of the smaller cluster, since
   * that is the one absorbed.
   *
   * Every question costs the same tap, so this is what makes one worth asking
   * over another. Ranking without it spent the owner's first six slots on pairs
   * of single-face strangers -- one photo each -- while a 257-face tile split
   * from its own 150-face other half sat at rank fourteen.
   */
  photosFixed: number;
  /**
   * This pair cleared its merge bar on face evidence and is held apart ONLY by
   * having been photographed together.
   *
   * This is asked first because it identifies exactly what the user can repair:
   * a similarity-qualified join vetoed by same-photo evidence. The current
   * answer audit contains zero labelled pairs of this shape, so no accuracy or
   * prevalence claim rests on that ordering.
   */
  blockedByCoOccurrence: boolean;
};

/**
 * Operational review-queue bucket, not a merge threshold or a measured class
 * boundary. It keeps low-co-occurrence questions together for deterministic
 * ranking, but the labelled set is still too small to attach significance: 89
 * answers currently constrain one co-occurring pair, labelled different people
 * at 2.7%, and zero over-bar co-occurrence blocks. Changing this constant from
 * those labels would manufacture a conclusion from one example.
 *
 * Neither band means anything on a denominator of one or two. The screen guards
 * that separately (`MIN_APPEARANCES_FOR_A_CONCLUSION`) — 1 shared photo of 1 is
 * 100%, which is the DOUBLE-DETECTION signature and not the frequent-together
 * one, and reading it through this constant states the opposite conclusion.
 */
export const RARE_MERGE_CO_OCCURRENCE_RATE = 0.05;

/** Above this, the evidence sentence warns that two people may travel together. */
export const FREQUENT_MERGE_CO_OCCURRENCE_RATE = 0.15;

/**
 * People in a frame beyond which "these two are one person" stops being a
 * question worth asking.
 *
 * Three, because two is where the doubt actually lives: a frame the library
 * reads as holding one or two people is where a repeat detection, a mirror or a
 * photo of a printed photo can masquerade as a second person. Add a third and
 * the frame is a group, and a group photo containing two faces contains two
 * people. Used only to withhold REVIEW QUESTIONS; the cannot-link itself is
 * untouched, so the pair stays unmerged either way.
 */
export const CROWDED_PHOTO_PEOPLE = 3;

function mergeCoOccurrenceRate(suggestion: MergeSuggestion): number {
  return suggestion.appearances > 0
    ? suggestion.sharedAssets / suggestion.appearances
    : Number.POSITIVE_INFINITY;
}

/**
 * Pairs of people that ALMOST merged, ranked by how close they came.
 *
 * Getting a threshold exactly right for every face in a library is not
 * achievable -- this codebase has already proved that in both directions, with
 * a bar so tight one person became several and a past relaxation that produced
 * a single tile holding 2,164 photos. The way out is not a better constant. It
 * is to stop guessing on the pairs the measurement cannot call, and ask.
 *
 * So this returns exactly the pairs sitting just under the bar that governs
 * them: close enough to be worth a human glance, not close enough for the app
 * to act alone. A confirmed pair becomes a must-link, which outranks every
 * measured bar and survives future reclusters, so each answer is permanent.
 *
 * Deliberately excluded, because they are answers rather than questions:
 *   - pairs already OVER their bar, which merge on their own
 *   - two faces from one photo below the mirror/panorama exception, which is
 *     near-certain evidence of two different people
 *   - anything the user has already ruled out
 *
 * O(people^2) in linkage calls, the same shape as one consolidation sweep, so
 * this belongs behind a deliberate action and not on a startup path.
 */
export function suggestMerges(
  people: readonly Person[],
  opts: ClusterOptions & { limit?: number; floor?: number } = {},
): MergeSuggestion[] {
  const limit = Number.isFinite(opts.limit) ? Math.max(0, opts.limit as number) : 20;
  if (limit === 0 || people.length < 2) return [];
  const bars = mergeBars(opts);
  // Below this a pair is not a near miss, it is two different people, and
  // offering it wastes the one thing this feature spends: the user's judgement.
  const floor = Number.isFinite(opts.floor)
    ? (opts.floor as number)
    : bars.evidenced * 0.6;

  const mutable = people.map(mutablePerson);
  const blocked = new Set<string>();
  const pairName = (i: number, j: number): string => `${i}:${j}`;
  const resolved = resolveConstraints(mutable, opts.constraints ?? [], bars);
  for (const [i, j] of resolved.cannot) {
    blocked.add(pairName(Math.min(i, j), Math.max(i, j)));
  }

  // How many distinct people the library already believes are in each photo.
  // Built once; the inner loop is O(people^2) and must not walk this.
  const peopleInPhoto = new Map<string, number>();
  for (const person of mutable) {
    for (const assetId of person.assetIdSet) {
      peopleInPhoto.set(assetId, (peopleInPhoto.get(assetId) ?? 0) + 1);
    }
  }

  const found: MergeSuggestion[] = [];
  for (let i = 0; i < mutable.length; i += 1) {
    for (let j = i + 1; j < mutable.length; j += 1) {
      if (blocked.has(pairName(i, j))) continue;
      const a = mutable[i];
      const b = mutable[j];
      if (
        a.embeddingKind !== b.embeddingKind ||
        a.centroid.length === 0 ||
        a.centroid.length !== b.centroid.length
      ) {
        continue;
      }
      // Nothing under the floor can become a suggestion, so the dot product is
      // abandoned as soon as that is provable. Worth having, but modestly:
      // measured over 2.36M pairs at the device's shape it is 1,655ms -> 1,295ms
      // with an identical suggestion list. Only 1.28x, because the floor sits
      // far below the bar and the Cauchy-Schwarz remainder over 512 near-
      // orthogonal dimensions stays larger than it until the sixth block of
      // eight. Assignment prunes far harder, since there the bar climbs to the
      // best match found so far rather than staying at a fixed floor.
      const similarity = boundedSimilarity(
        a.centroid,
        a.inverse,
        a.suffix,
        b.centroid,
        b.inverse,
        b.suffix,
        floor,
      );
      if (similarity === Number.NEGATIVE_INFINITY || similarity < floor) continue;
      // The same bar `scanPair` would have applied, so a suggestion is exactly
      // "this pair failed the test the app actually ran" -- not a second,
      // looser opinion invented for the UI.
      const evidenced =
        a.faceCount >= MERGE_EVIDENCE_MIN_FACES &&
        b.faceCount >= MERGE_EVIDENCE_MIN_FACES;
      const gap = spanGap(a, b);
      const nearInTime = gap !== undefined && gap <= TEMPORAL_MERGE_WINDOW_MS;
      const identityBar = evidenced
        ? nearInTime
          ? Math.min(bars.evidenced, bars.temporal)
          : bars.evidenced
        : bars.identity;
      const bar = a.embeddingKind === "identity" ? identityBar : bars.perceptual;
      const sharedAssets = countSharedAssets(a.assetIdSet, b.assetIdSet);
      // Co-occurrence is now an absolute veto, so every shared photo produces a
      // question rather than some of them being crossed automatically.
      const vetoed = sharedAssets > 0;
      // Over the bar and NOT vetoed: merging handles it, so it is not a
      // question. Over the bar and vetoed is the most valuable question there
      // is, and used to be silently dropped here -- see `blockedByCoOccurrence`.
      if (similarity >= bar && !vetoed) continue;
      // Two faces that appear NOWHERE except one photo they share carry no
      // information for the user to act on, so they are not asked about.
      //
      // Measured on the owner's live index: 48 such pairs, and they come from
      // exactly NINE images -- two screenshots of this app's own photo grid,
      // one ChatGPT download, and six WhatsApp pictures that are photographs OF
      // PRINTED PHOTO ALBUMS, where the same relatives appear in each printed
      // photo inside the frame. None of the nine is an ordinary photograph. One
      // grid screenshot holds 20 detected "faces" and generated 39 of the 48
      // pairs on its own.
      //
      // Neither side has any history: one face each, one photo each, the same
      // photo. So whichever way it is answered, one composite image changes and
      // nothing else -- while the question itself is unanswerable, because if it
      // IS one face found twice the two crops are the same pixels. Dropping them
      // costs nothing measurable and removes every question of this shape.
      //
      // Deliberately NOT a threshold change. The pair is still blocked from
      // merging by the same-photo cannot-link exactly as before; it is only
      // withheld from the review. Any pair with real history behind it still
      // gets asked, and the screen now shows it the photograph.
      const strangersInOnePhoto =
        vetoed &&
        a.faceCount === 1 &&
        b.faceCount === 1 &&
        a.assetIdSet.size === 1 &&
        b.assetIdSet.size === 1;
      if (strangersInOnePhoto) continue;
      // In a crowded photo, two faces are two people, and asking is absurd.
      //
      // The owner made this point after being shown a party photograph holding
      // five or six people under the question — "of course there are more than
      // one people in the image, any basic ML model will tell that". He is
      // right, and the app already knows it: it has placed several distinct
      // people in that frame.
      //
      // The earlier live-index census attached exact rates to this shape, but
      // its answer store did not supply identity-level labels. The safe fact is
      // structural: three or more distinct clusters make this a group photo,
      // while the two-person frame remains the ambiguous shape worth asking.
      //
      // Safe under this codebase's own invariant: withholding a question can
      // only ever leave two records SPLIT, never fused. A split is repairable by
      // any later pass; a fusion is not. So the cost of being wrong here is a
      // tile that stays divided, against the cost of asking him thousands of
      // questions whose answer he can already see is "obviously two people".
      //
      // MIN across the shared photos, not max: if any one of them holds two
      // people or fewer, that frame carries real doubt and is worth asking about.
      if (vetoed) {
        let leastCrowded = Number.POSITIVE_INFINITY;
        for (const assetId of a.assetIdSet) {
          if (!b.assetIdSet.has(assetId)) continue;
          leastCrowded = Math.min(
            leastCrowded,
            peopleInPhoto.get(assetId) ?? 0,
          );
        }
        if (leastCrowded >= CROWDED_PHOTO_PEOPLE) continue;
      }
      const [first, second] =
        a.faceCount >= b.faceCount ? [a, b] : [b, a];
      found.push({
        a: first.id,
        b: second.id,
        similarity,
        bar,
        sharedAssets,
        appearances: Math.min(first.assetIdSet.size, second.assetIdSet.size),
        photosFixed: Math.min(first.faceCount, second.faceCount),
        blockedByCoOccurrence: vetoed && similarity >= bar,
      });
    }
  }

  // Vetoed pairs first: they cleared the bar on face evidence and are held
  // apart only by co-occurrence, so they are the ones where the app is most
  // likely to be wrong and the user's answer is worth most.
  //
  // Within that group, keep the low-co-occurrence bucket first. This is a review
  // heuristic only: the current labelled set contains one co-occurring pair,
  // which is nowhere near enough to estimate which answer either bucket gets.
  // The queue asks the user; the bucket never authorises a merge.
  //
  // Within each population, the biggest repair comes first. Every question
  // costs the same tap, so the smaller side's size is how much that tap can put
  // right. The exact rate then breaks ties before similarity.
  //
  // The pairs BELOW their bar keep similarity first. There confidence IS the
  // binding constraint, and a wrong merge is unrecoverable, so a large tile is
  // a reason for care rather than for haste.
  found.sort(
    (x, y) =>
      Number(y.blockedByCoOccurrence) - Number(x.blockedByCoOccurrence) ||
      (x.blockedByCoOccurrence
        ? Number(
            mergeCoOccurrenceRate(y) <= RARE_MERGE_CO_OCCURRENCE_RATE,
          ) -
            Number(
              mergeCoOccurrenceRate(x) <= RARE_MERGE_CO_OCCURRENCE_RATE,
            ) ||
          y.photosFixed - x.photosFixed ||
          mergeCoOccurrenceRate(x) - mergeCoOccurrenceRate(y)
        : 0) ||
      y.similarity - x.similarity ||
      (x.a < y.a ? -1 : x.a > y.a ? 1 : x.b < y.b ? -1 : x.b > y.b ? 1 : 0),
  );
  return found.slice(0, limit);
}

/**
 * Bars for the graph clusterer, one per age band.
 *
 * A SINGLE bar cannot serve this library, and that is measured rather than
 * assumed. On 10,298 of Rohan's faces with four identities he verified himself:
 * at 0.40 Aastha lands in one tile but Ved and Krishiv weld into one person; at
 * 0.46 the three children separate cleanly but Aastha shatters into eight tiles.
 * An adult's face drifts over years, so it needs a forgiving bar; two children
 * of similar age genuinely resemble each other, so they need a strict one.
 *
 * Splitting the bar by age settled both at once -- Aastha, Ved, Avika and
 * Krishiv each in exactly ONE tile with zero fusions -- and it held across every
 * pairing tried, so this is a wide plateau rather than a tuned coincidence.
 */
export const GRAPH_ADULT_SIMILARITY = 0.4;
export const GRAPH_BABY_SIMILARITY = 0.5;

/** Label-propagation passes. Convergence was reached well inside this on real data. */
const GRAPH_ROUNDS = 20;

/**
 * Deterministic pseudo-random order.
 *
 * Chinese Whispers needs the visit order shuffled or labels propagate along
 * whatever order the array happens to have. It must NOT be genuinely random:
 * this app shows the user named people and remembers merges they confirmed, and
 * a grouping that reshuffles on every scan is not something anyone can trust.
 * Seeded, so the same photographs always produce the same people.
 */
function seededOrder(count: number, seed: number): number[] {
  const order = Array.from({ length: count }, (_, i) => i);
  let state = seed >>> 0 || 1;
  for (let i = count - 1; i > 0; i -= 1) {
    state = (state * 1664525 + 1013904223) >>> 0;
    const j = state % (i + 1);
    const swap = order[i];
    order[i] = order[j];
    order[j] = swap;
  }
  return order;
}

/**
 * Cluster faces as a graph rather than by greedy assignment.
 *
 * The greedy pass in `extendFaceClusters` decides each face against the clusters
 * that exist at the moment it arrives, and never revisits that decision. It is
 * cheap and incremental, but on the measured library it left Aastha in dozens of
 * tiles: a face that arrives when a person owns only two photographs may miss
 * the bar, start its own person, and never come back.
 *
 * Chinese Whispers instead links every sufficiently similar pair and lets labels
 * propagate. That is what unites a person across years -- her face at the start
 * and at the end need never be similar to EACH OTHER, only to the photographs in
 * between. On Rohan's own library this took Aastha from 27 tiles to 1, and he
 * confirmed the resulting tile was pure.
 *
 * COST: building the graph is O(n^2) in the number of faces, and that dominates
 * -- the propagation itself is cheap. `boundedSimilarity` abandons a hopeless
 * pair after as little as an eighth of the dot product, which is what makes this
 * tolerable, but it is still quadratic. This is a deliberate rebuild, not
 * something to run on every scan.
 *
 * TWO THINGS THIS PASS OWES THE GREEDY ONE, both of which it silently did not
 * pay until 08-29, and neither of which any passing test noticed -- every
 * constraint test called `clusterFaces` directly, so they all went on proving
 * things about the path that no longer ships:
 *
 *   1. The user's answers. `clusterFaces` applies them inside its merge sweep;
 *      this pass deliberately runs no sweep, so they were simply dropped and
 *      every "yes, these two are the same person" was forgotten on the next
 *      full recluster. See `applyForcedMerges`.
 *   2. The perceptual fallback. `usable` keeps only identity faces, so on a
 *      phone where the identity model fails to load, People went from
 *      "grouped conservatively" to "empty". See `perceptualPeople`.
 */
export function clusterFacesByGraph(
  observations: FaceObservation[],
  opts: ClusterOptions & { seed?: number } = {},
): Person[] {
  const usable = observations.filter((o) => o.embeddingKind === "identity");
  const count = usable.length;
  if (count === 0) return withPerceptual([], observations, opts);

  // Timed per stage, and reported even on the happy path. The whole-library
  // regroup was measured at ~6 minutes wall against only ~118% average CPU --
  // far too little for eight native worker threads -- so which stage actually
  // costs the time is genuinely unknown. Averages over a whole process cannot
  // answer that; these three numbers can.
  const preparedAt = Date.now();
  const { vectors, inverses, suffixes, bars } = prepareGraph(usable, opts);
  const prepareMs = Date.now() - preparedAt;

  // The same algorithm, run where a hot numeric loop can actually be compiled.
  // Building this graph is 156 million pairs of 512 dimensions on the owner's
  // library: Node does it in 142 seconds, Hermes did not finish it in seventeen
  // minutes of phone CPU. Everything below is the identical TypeScript, kept
  // working and kept correct, for every build that has no native side.
  const graphAt = Date.now();
  const nativeLabels = takeStashedLabels(usable, opts.seed ?? 1);
  const labels =
    nativeLabels ?? propagateLabels(usable, vectors, inverses, suffixes, bars, opts);
  const graphMs = Date.now() - graphAt;

  // ONE tail, deliberately. The native and TypeScript label sources used to
  // have an exit each, which is how `applyForcedMerges` could be added to one
  // and forgotten on the other -- and the one Node cannot reach is the one that
  // ships, so no test would ever have said so. Everything that turns labels
  // into people now happens exactly once, whoever produced the labels.
  const peopleAt = Date.now();
  const people = peopleFromGraphLabels(
    usable,
    vectors,
    inverses,
    suffixes,
    labels,
    opts,
  );
  const forced = applyForcedMerges(people, opts);
  console.log(
    `[PhoteoFaceCluster] ${nativeLabels ? "native" : "js"} faces=${count} ` +
      `prepare=${prepareMs}ms graph=${graphMs}ms people=${Date.now() - peopleAt}ms ` +
      `tiles=${people.length} forced=${forced}/${(opts.constraints ?? []).length}`,
  );
  return withPerceptual(people, observations, opts);
}

/**
 * Chinese Whispers in TypeScript: build the edges, then propagate labels.
 *
 * The same algorithm the native side runs, kept working and kept correct for
 * every build that has no native side. Extracted from `clusterFacesByGraph` so
 * that function has a single tail -- see the comment at that tail for why that
 * matters more than it looks.
 */
function propagateLabels(
  usable: FaceObservation[],
  vectors: number[][],
  inverses: Float64Array,
  suffixes: Float64Array[],
  bars: Float64Array,
  opts: ClusterOptions & { seed?: number },
): Int32Array {
  const count = usable.length;
  const neighbours: { at: number; weight: number }[][] = Array.from(
    { length: count },
    () => [],
  );
  for (let i = 0; i < count; i += 1) {
    for (let j = i + 1; j < count; j += 1) {
      // Two faces in one photograph are different people, so they are never
      // linked -- the same rule the greedy pass enforces before its dot product.
      if (usable[i].assetId === usable[j].assetId) continue;
      // The stricter endpoint wins, so a child can never be pulled in on an
      // adult's more forgiving terms.
      const required = bars[i] > bars[j] ? bars[i] : bars[j];
      const similarity = boundedSimilarity(
        vectors[i],
        inverses[i],
        suffixes[i],
        vectors[j],
        inverses[j],
        suffixes[j],
        required,
      );
      if (similarity === Number.NEGATIVE_INFINITY || similarity < required) {
        continue;
      }
      neighbours[i].push({ at: j, weight: similarity });
      neighbours[j].push({ at: i, weight: similarity });
    }
  }

  const labels = new Int32Array(count);
  for (let i = 0; i < count; i += 1) labels[i] = i;
  const order = seededOrder(count, opts.seed ?? 1);
  for (let round = 0; round < GRAPH_ROUNDS; round += 1) {
    let moved = 0;
    for (const i of order) {
      const near = neighbours[i];
      if (near.length === 0) continue;
      const weight = new Map<number, number>();
      for (const { at, weight: w } of near) {
        const label = labels[at];
        weight.set(label, (weight.get(label) ?? 0) + w);
      }
      let best = labels[i];
      let bestWeight = -Infinity;
      for (const [label, total] of weight) {
        // Ties broken by the smaller label, never randomly: two runs over the
        // same photographs have to agree.
        if (total > bestWeight || (total === bestWeight && label < best)) {
          best = label;
          bestWeight = total;
        }
      }
      if (best !== labels[i]) {
        labels[i] = best;
        moved += 1;
      }
    }
    if (moved === 0) break;
  }
  return labels;
}

/**
 * The user's "yes, these two are the same person", applied to a finished
 * clustering.
 *
 * `clusterFaces` does this inside its merge sweep. The graph pass runs no
 * sweep -- deliberately, since label propagation already does the sweep's job
 * -- and so it dropped the answers on the floor. The consequence was not
 * subtle: an answer held until the next full recluster and then vanished, and
 * a full recluster is exactly what a graph rebuild is. Since a user-confirmed
 * merge is the ONLY repair for the same-person splits that no threshold can
 * fix, this was discarding the one signal that works.
 *
 * Order matches the greedy path: resolve against the pre-merge clusters, then
 * translate to ids before touching anything, because `mergeExistingPeople`
 * splices and every later index would address a different person. A missing id
 * is the transitive case (A=B and B=C) and is not an error.
 *
 * Cannot-links are NOT applied here, and that is a real limit rather than an
 * oversight: undoing a label-propagation join means splitting a cluster, which
 * this function cannot do. `resolveConstraints` already reports such a pair as
 * a contradiction, and the same-photo rule -- the cannot-link that fires in
 * practice -- is enforced when the graph edges are built, not afterwards.
 *
 * Returns how many merges it forced, so the log line can show whether the
 * answers reached the clustering at all.
 */
function applyForcedMerges(people: Person[], opts: ClusterOptions): number {
  const constraints = opts.constraints ?? [];
  if (constraints.length === 0) return 0;
  const resolved = resolveConstraints(people, constraints, mergeBars(opts));
  const forced = resolved.must.map(
    ([ai, bi]) => [people[ai]?.id, people[bi]?.id] as const,
  );
  let merged = 0;
  for (const [aId, bId] of forced) {
    if (!aId || !bId) continue;
    const i = people.findIndex((person) => person.id === aId);
    const j = people.findIndex((person) => person.id === bId);
    if (i === -1 || j === -1 || i === j) continue;
    if (mergeExistingPeople(people, i, j, opts.onMerge)) merged += 1;
  }
  return merged;
}

/**
 * Put the perceptual-fallback faces back, grouped by the greedy pass.
 *
 * The graph pass keeps only identity embeddings, and rightly so: Chinese
 * Whispers propagates a label along a chain of similar faces, and a chain built
 * out of an 8x8 luma grid would walk between strangers. But dropping them
 * entirely is worse than grouping them badly -- a phone where the identity
 * model fails to load showed NO people at all, where before it showed
 * conservatively-grouped ones.
 *
 * They go through `clusterFaces`, which already handles them and already
 * refuses to compare across kinds, rather than through a second copy of that
 * logic here. In the normal case there are none of these and this costs one
 * `some` over the observations.
 */
function withPerceptual(
  people: Person[],
  observations: readonly FaceObservation[],
  opts: ClusterOptions,
): Person[] {
  const fallback = observations.filter(
    (observation) => observation.embeddingKind === "perceptual",
  );
  if (fallback.length === 0) return people;
  // Renumbered from where the graph pass stopped, so no two people share an id.
  const extra = clusterFaces(fallback, opts).map((person, index) => ({
    ...person,
    id: `person-${people.length + index + 1}`,
  }));
  return people.concat(extra);
}

/**
 * Everything both clustering paths need per face, computed once.
 *
 * Shared rather than duplicated because the native and TypeScript paths must
 * cluster the SAME vectors on the SAME bars. Two copies of this loop that drift
 * apart would show the user different people depending on which path ran, and
 * the drift would be invisible until somebody's tiles split.
 */
/**
 * Just the per-face bars, without materialising a single unit vector.
 *
 * `prepareGraph` costs 5.9 SECONDS on the owner's 18,165 faces (measured on
 * device), and the precompute pass threw all of it away except this one array --
 * it needs the bars to hand to the native side and nothing else. Running the
 * full preparation twice put twelve seconds of avoidable work on the JS thread.
 *
 * The saving comes from the age probe being a plain dot product. For a stored
 * vector v and its unit form v/|v|:
 *
 *   z = intercept + sum_d c_d * (v_d / |v|)  ==  intercept + dot(c, v) / |v|
 *
 * so the normalisation can be divided out at the end instead of being applied to
 * all 512 components first. That turns three passes and a 512-element array per
 * face into one pass over the stored bytes with two accumulators.
 *
 * Bit-for-bit agreement with `babyScore(unitEmbedding(...))` is not claimed --
 * the two sum in a different order, so they can differ in the last ulp. That is
 * safe HERE and nowhere else: the result is compared against `BABY_SCORE_CUT`,
 * and a face within one ulp of the cut is one the probe is not confident about
 * anyway. `face-age-bars.test.ts` pins that the two paths choose the same bar
 * for every face in the fixture.
 */
function graphBarsOnly(
  usable: FaceObservation[],
  opts: ClusterOptions,
): Float64Array {
  const count = usable.length;
  const bars = new Float64Array(count);
  const adultBar = Number.isFinite(opts.threshold)
    ? (opts.threshold as number)
    : GRAPH_ADULT_SIMILARITY;
  for (let i = 0; i < count; i += 1) {
    const raw = dequantized(usable[i].embedding);
    if (raw.length !== AGE_COEFFICIENTS.length) {
      bars[i] = adultBar;
      continue;
    }
    let dot = 0;
    let squared = 0;
    for (let d = 0; d < raw.length; d += 1) {
      const value = raw[d];
      dot += value * AGE_COEFFICIENTS[d];
      squared += value * value;
    }
    const norm = Math.sqrt(squared);
    const z = AGE_INTERCEPT + (norm > Number.EPSILON ? dot / norm : 0);
    bars[i] =
      1 / (1 + Math.exp(-z)) > BABY_SCORE_CUT ? GRAPH_BABY_SIMILARITY : adultBar;
  }
  return bars;
}

function prepareGraph(
  usable: FaceObservation[],
  opts: ClusterOptions,
): {
  vectors: number[][];
  inverses: Float64Array;
  suffixes: Float64Array[];
  bars: Float64Array;
} {
  const count = usable.length;
  const vectors: number[][] = new Array(count);
  const inverses = new Float64Array(count);
  const suffixes: Float64Array[] = new Array(count);
  const bars = new Float64Array(count);
  const adultBar = Number.isFinite(opts.threshold)
    ? (opts.threshold as number)
    : GRAPH_ADULT_SIMILARITY;

  for (let i = 0; i < count; i += 1) {
    const embedding = unitEmbedding(usable[i].embedding);
    vectors[i] = embedding;
    inverses[i] = comparisonInverse(embedding);
    suffixes[i] = suffixNorms(embedding);
    // A child gets the strict bar. Erring toward "child" is the safe direction:
    // it costs a split, which a merge question repairs, where the other mistake
    // fuses two children and nothing repairs that.
    bars[i] =
      babyScore(embedding) > BABY_SCORE_CUT ? GRAPH_BABY_SIMILARITY : adultBar;
  }
  return { vectors, inverses, suffixes, bars };
}

/** Turns one label per face into people, whichever path produced the labels. */
function peopleFromGraphLabels(
  usable: FaceObservation[],
  vectors: number[][],
  inverses: Float64Array,
  suffixes: Float64Array[],
  labels: ArrayLike<number>,
  opts: ClusterOptions,
): Person[] {
  const count = usable.length;
  const groups = new Map<number, number[]>();
  for (let i = 0; i < count; i += 1) {
    const list = groups.get(labels[i]);
    if (list) list.push(i);
    else groups.set(labels[i], [i]);
  }

  // Emit in a stable order so person ids do not shuffle between identical runs.
  const ordered = [...groups.values()].sort((a, b) => a[0] - b[0]);
  const people: MutablePerson[] = [];
  let nextPersonNumber = 1;
  for (const members of ordered) {
    const first = usable[members[0]];
    if (members.length === 1 && first.seedable === false) continue;
    const embedding = vectors[members[0]];
    const person: MutablePerson = {
      id: `person-${nextPersonNumber++}`,
      faceCount: 1,
      assetIds: [first.assetId],
      centroid: embedding.slice(),
      embeddingKind: first.embeddingKind,
      assetIdSet: new Set([first.assetId]),
      inverse: inverses[members[0]],
      suffix: suffixes[members[0]],
      weightSum: centroidWeight(first),
      firstAt: spanTime(first.capturedAt),
      lastAt: spanTime(first.capturedAt),
    };
    opts.onAssign?.(first, person.id);
    for (let k = 1; k < members.length; k += 1) {
      const observation = usable[members[k]];
      const weight = centroidWeight(observation);
      person.centroid = updateCentroid(
        person.centroid,
        vectors[members[k]],
        person.weightSum,
        weight,
      );
      refreshCentroidMagnitudes(person);
      person.weightSum += weight;
      person.faceCount += 1;
      widenSpan(person, observation.capturedAt);
      if (!person.assetIdSet.has(observation.assetId)) {
        person.assetIdSet.add(observation.assetId);
        person.assetIds.push(observation.assetId);
      }
      opts.onAssign?.(observation, person.id);
    }
    people.push(person);
  }
  return people.map(publicPerson);
}

const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/**
 * Base64 for a byte array, written out rather than reached for.
 *
 * `btoa` is not reliably present in React Native, and `Buffer` is not present at
 * all. Emitted in chunks and joined because the payload is millions of bytes and
 * repeated string concatenation on that scale is quadratic.
 */
function encodeBase64(bytes: Uint8Array): string {
  const chunks: string[] = [];
  let piece = "";
  let index = 0;
  for (; index + 2 < bytes.length; index += 3) {
    const triple = (bytes[index] << 16) | (bytes[index + 1] << 8) | bytes[index + 2];
    piece +=
      BASE64_ALPHABET[(triple >> 18) & 63] +
      BASE64_ALPHABET[(triple >> 12) & 63] +
      BASE64_ALPHABET[(triple >> 6) & 63] +
      BASE64_ALPHABET[triple & 63];
    if (piece.length >= 8192) {
      chunks.push(piece);
      piece = "";
    }
  }
  const left = bytes.length - index;
  if (left === 1) {
    const triple = bytes[index] << 16;
    piece += `${BASE64_ALPHABET[(triple >> 18) & 63]}${BASE64_ALPHABET[(triple >> 12) & 63]}==`;
  } else if (left === 2) {
    const triple = (bytes[index] << 16) | (bytes[index + 1] << 8);
    piece += `${BASE64_ALPHABET[(triple >> 18) & 63]}${BASE64_ALPHABET[(triple >> 12) & 63]}${BASE64_ALPHABET[(triple >> 6) & 63]}=`;
  }
  chunks.push(piece);
  return chunks.join("");
}

/**
 * The stored int8 bytes for every face, or null if any face is not stored that
 * way.
 *
 * The native dot product is EXACT integer arithmetic over the stored bytes --
 * the 1/127 dequantisation scale cancels out of a cosine entirely -- which is
 * both why it is fast and why it needs the bytes rather than the floats. A face
 * carrying a full-precision `number[]` cannot go down that path without being
 * re-quantised, and quietly rounding somebody's embedding to make it fit is
 * exactly the kind of invisible change that moves a person between tiles. So
 * that case declines the native path instead.
 */
function packEmbeddings(
  usable: FaceObservation[],
  dim: number,
): Uint8Array | null {
  const packed = new Uint8Array(usable.length * dim);
  for (let i = 0; i < usable.length; i += 1) {
    const embedding = usable[i].embedding;
    if (!(embedding instanceof Int8Array) || embedding.length !== dim) {
      return null;
    }
    packed.set(new Uint8Array(embedding.buffer, embedding.byteOffset, dim), i * dim);
  }
  return packed;
}

/**
 * The graph built natively, or null when this device cannot.
 *
 * Only the O(n^2) edge build and the label propagation move; the vectors, the
 * bars and the people are all still built by the same TypeScript above. That
 * split is the point -- the native side owns the one part that is purely
 * arithmetic and has no judgement in it, so there is no second copy of any
 * threshold or any rule to drift out of step.
 */
/**
 * Labels computed off the JS thread, waiting for the clusterer to collect them.
 *
 * `rebuildPeople` has six callers and is synchronous; making the whole chain
 * async to move ONE call off-thread would be a large change to code that has no
 * other reason to move. So the expensive part is computed ahead of time by
 * `precomputeGraphLabels`, which IS async, and left here for the synchronous
 * clusterer to pick up.
 *
 * Keyed, not just stored. A stash that silently belonged to a different set of
 * faces would relabel the library against a roster it never saw, which is worse
 * than being slow.
 */
let stashedLabels: { key: string; labels: number[] } | null = null;

/**
 * Identifies the exact face set a stash was computed for.
 *
 * Folds every face id rather than trusting the count: a scan that adds one face
 * and drops another leaves the count identical while changing who is in the
 * graph, and that is precisely the case where reusing labels would be wrong.
 */
function graphStashKey(usable: FaceObservation[], seed: number): string {
  let hash = 0;
  for (let i = 0; i < usable.length; i += 1) {
    // assetId plus POSITION: several faces share one photograph, so the id
    // alone cannot distinguish them, and the graph depends on their order.
    const id = usable[i].assetId;
    hash = (hash * 31 + i) | 0;
    for (let c = 0; c < id.length; c += 1) {
      hash = (hash * 31 + id.charCodeAt(c)) | 0;
    }
  }
  return `${usable.length}:${seed}:${hash}`;
}

/**
 * Runs the native graph pass OFF the JS thread and stashes the result.
 *
 * This is the whole reason the native call is an `AsyncFunction`. Declared as a
 * synchronous one it executed on the JS thread, so a whole-library regroup --
 * around six minutes on the owner's library -- froze the app for its entire
 * duration. Faster than the seventeen minutes it replaced, and still a freeze,
 * which is the thing he actually reports.
 *
 * Returns whether a stash is now waiting. False means the sync path will do
 * whatever it would have done anyway, so nothing here can break clustering --
 * only speed it up.
 */
export async function precomputeGraphLabels(
  observations: FaceObservation[],
  opts: ClusterOptions & { seed?: number } = {},
): Promise<boolean> {
  const usable = observations.filter((o) => o.embeddingKind === "identity");
  if (usable.length === 0) return false;
  const seed = opts.seed ?? 1;
  // Bars only: the precompute discards everything else prepareGraph builds, and
  // building it cost 5.9s of JS thread on the owner's library, measured on device.
  const bars = graphBarsOnly(usable, opts);
  const labels = await graphLabelsNatively(usable, bars, seed);
  if (!labels || labels.length !== usable.length) return false;
  stashedLabels = { key: graphStashKey(usable, seed), labels };
  return true;
}

/** The stash, if it was computed for exactly these faces. Consumed once. */
function takeStashedLabels(
  usable: FaceObservation[],
  seed: number,
): number[] | null {
  const stash = stashedLabels;
  if (!stash) return null;
  // Cleared whether or not it matched: a stash that did not match is stale by
  // definition, and holding it would let a later rebuild collect labels older
  // than the one that just rejected them.
  stashedLabels = null;
  return stash.key === graphStashKey(usable, seed) ? stash.labels : null;
}

function graphLabelsNatively(
  usable: FaceObservation[],
  bars: Float64Array,
  seed: number,
): Promise<number[] | null> {
  const dim = usable[0].embedding.length;
  if (dim <= 0) return Promise.resolve(null);
  const packed = packEmbeddings(usable, dim);
  if (!packed) return Promise.resolve(null);

  // Photograph identity travels as an int: the native side only ever asks
  // "same photo?", and comparing ints beats shipping and hashing strings.
  const assetNumbers = new Map<string, number>();
  const assetGroup: number[] = new Array(usable.length);
  for (let i = 0; i < usable.length; i += 1) {
    const assetId = usable[i].assetId;
    let number = assetNumbers.get(assetId);
    if (number === undefined) {
      number = assetNumbers.size;
      assetNumbers.set(assetId, number);
    }
    assetGroup[i] = number;
  }

  return clusterFacesNatively(
    encodeBase64(packed),
    dim,
    assetGroup,
    Array.from(bars),
    seed,
    GRAPH_ROUNDS,
  );
}

export type { FaceObservation, Person } from "./types";
