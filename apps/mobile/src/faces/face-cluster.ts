import type { FaceObservation, Person } from "./types";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { resolveConstraints, type FaceConstraint } from "./face-constraints.ts";

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
 * Same-photo cannot-link escape hatch.
 *
 * Two faces detected in ONE photo are almost never the same person, so a shared
 * asset id is a hard constraint: without it a parent absorbs their child and
 * siblings collapse into a single tile, which is the worst-looking failure in a
 * family library. The exception is a face that legitimately appears twice in a
 * single frame — mirrors, panorama stitches, collages, photos-of-photos — and
 * those land far above any identity bar; anything below the bar stays split even
 * when the identity threshold passes.
 *
 * Ported with the w600k_mbf swap by holding its old offset above the merge bar
 * (0.85 against a 0.72 merge). Unlike the identity bar this offset is NOT
 * separately measured — there is no labelled corpus of mirrors and collages to
 * measure against. It fails safe: too high only leaves a legitimate
 * double-appearance split into two people, which the user can merge by hand,
 * whereas too low fuses a parent with their child irreversibly.
 */
export const SAME_PHOTO_EXCEPTION_SIMILARITY = 0.72;

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
function unitEmbedding(embedding: number[]): number[] {
  const length = magnitude(embedding);
  return length > Number.EPSILON && Math.abs(length - 1) > 1e-9
    ? embedding.map((value) => value / length)
    : embedding;
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
function comparisonInverse(values: number[]): number {
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
function scaledSimilarity(
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

/** Grows a cluster's capture-time span to include one more face. */
function widenSpan(person: MutablePerson, capturedAt: number | undefined): void {
  if (!Number.isFinite(capturedAt)) return;
  const at = capturedAt as number;
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
    firstAt:
      typeof stored.firstAt === "number" && Number.isFinite(stored.firstAt)
        ? stored.firstAt
        : undefined,
    lastAt:
      typeof stored.lastAt === "number" && Number.isFinite(stored.lastAt)
        ? stored.lastAt
        : undefined,
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
        // A person holding a face from this photo needs the much higher
        // mirror/panorama bar, so asking for `required` here would abandon the
        // dot product before it could be compared against that bar. Only the
        // exception is at stake -- such a person can never be chosen on
        // `required` alone -- so it is the exception that must be cleared.
        person.assetIdSet.has(observation.assetId)
          ? SAME_PHOTO_EXCEPTION_SIMILARITY
          : required,
      );
      if (similarity === Number.NEGATIVE_INFINITY) continue;
      // Cannot-link: this person already owns a face from this very photo, so
      // joining would fuse two people who merely posed together. Only the
      // mirror/panorama exception may cross it, and on average linkage that
      // exception now means what it says — the face must average 0.85 against
      // the WHOLE cluster, which a sprawling one can no longer fake.
      if (
        person.assetIdSet.has(observation.assetId) &&
        similarity < SAME_PHOTO_EXCEPTION_SIMILARITY
      ) {
        continue;
      }
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
        firstAt: observation.capturedAt,
        lastAt: observation.capturedAt,
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
    mergeSimilarPeople(
      people,
      identityMergeThreshold,
      perceptualThreshold,
      evidencedMergeThreshold,
      temporalMergeThreshold,
      opts.constraints ?? [],
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
 * stays a cannot-link below SAME_PHOTO_EXCEPTION_SIMILARITY, and the merged
 * asset set inherits both constraints, so identities cannot be chained through
 * a bridge cluster. Every round removes exactly one person, so the loop is
 * bounded by the initial people count. The older cluster (lower index) survives,
 * keeping surfaced ids stable between runs so the UI does not reshuffle.
 *
 * The cannot-link is FROZEN before the first merge, because a live re-check is
 * defeated by exactly the chain it is supposed to stop. It used to re-evaluate
 * `similarity >= SAME_PHOTO_EXCEPTION_SIMILARITY` against the current centroids,
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
  onMerge?: (absorbedPersonId: string, survivingPersonId: string) => void,
): void {
  const comparable = (a: MutablePerson, b: MutablePerson): boolean =>
    a.embeddingKind === b.embeddingKind &&
    a.centroid.length > 0 &&
    a.centroid.length === b.centroid.length;
  /** Mean cosine between every face of `a` and every face of `b`. */
  const linkage = (a: MutablePerson, b: MutablePerson): number =>
    scaledSimilarity(a.centroid, a.inverse, b.centroid, b.inverse);
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
  for (let i = 0; i < people.length; i += 1) {
    for (let j = i + 1; j < people.length; j += 1) {
      const a = people[i];
      const b = people[j];
      if (!comparable(a, b)) continue;
      if (!sharesAsset(a.assetIdSet, b.assetIdSet)) continue;
      if (linkage(a, b) >= SAME_PHOTO_EXCEPTION_SIMILARITY) continue;
      blocked[i].add(j);
      blocked[j].add(i);
    }
  }

  // The user's own judgements, layered on top of the measured ones. A "not the
  // same person" joins the same `blocked` structure the same-photo rule uses,
  // so it inherits through merges for free. A "same person" is applied as a
  // forced merge below, before any similarity is consulted at all — the whole
  // point is that it holds where the numbers disagree.
  const resolved = resolveConstraints(people, constraints);
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
    absorb(people, origins, blocked, keep, drop, onMerge);
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
    // Clusters that overlap or nearly touch in time get the temporal bar,
    // which is how an infant's months chain together: each neighbouring pair
    // clears it, the union spans wider, and the next neighbour comes into
    // range on the following iteration. Requires BOTH to carry real evidence,
    // so a stray two-face cluster cannot ride a date into somebody else.
    const gap = spanGap(a, b);
    const nearInTime = gap !== undefined && gap <= TEMPORAL_MERGE_WINDOW_MS;
    const identityBar = evidenced
      ? nearInTime
        ? Math.min(evidencedMergeThreshold, temporalMergeThreshold)
        : evidencedMergeThreshold
      : identityMergeThreshold;
    const threshold =
      a.embeddingKind === "identity" ? identityBar : perceptualThreshold;
    // `blocked` must be read on every survivor-row refresh because an absorb
    // unions both endpoints' inherited cannot-links.
    if (intersects(blocked[i], origins[j])) return;
    const similarity = linkage(a, b);
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
  for (let i = 0; i < people.length; i += 1) {
    for (let j = i + 1; j < people.length; j += 1) {
      scanPair(i, j);
    }
  }

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
   * its weight varies enormously: ten shared photos means two people who are
   * usually together, one shared photo out of five hundred faces is more often
   * a double detection -- a reflection, a poster, a photo of a photo.
   */
  sharedAssets: number;
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
   * Measured on the owner's library, every single pair that cleared its bar was
   * in this state -- 37 of 37 -- and 27 of them were vetoed by exactly ONE
   * shared photo. So the merge pass is not failing because its bars are too
   * strict; it is failing because co-occurrence is an absolute veto that a
   * single frame can trigger. These are the pairs where the app is most likely
   * to be wrong, so they are asked first.
   */
  blockedByCoOccurrence: boolean;
};

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
  const resolved = resolveConstraints(mutable, opts.constraints ?? []);
  for (const [i, j] of resolved.cannot) {
    blocked.add(pairName(Math.min(i, j), Math.max(i, j)));
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
      const vetoed =
        sharedAssets > 0 && similarity < SAME_PHOTO_EXCEPTION_SIMILARITY;
      // Over the bar and NOT vetoed: merging handles it, so it is not a
      // question. Over the bar and vetoed is the most valuable question there
      // is, and used to be silently dropped here -- see `blockedByCoOccurrence`.
      if (similarity >= bar && !vetoed) continue;
      const [first, second] =
        a.faceCount >= b.faceCount ? [a, b] : [b, a];
      found.push({
        a: first.id,
        b: second.id,
        similarity,
        bar,
        sharedAssets,
        photosFixed: Math.min(first.faceCount, second.faceCount),
        blockedByCoOccurrence: vetoed && similarity >= bar,
      });
    }
  }

  // Vetoed pairs first: they cleared the bar on face evidence and are held
  // apart only by co-occurrence, so they are the ones where the app is most
  // likely to be wrong and the user's answer is worth most.
  //
  // Within that group, the biggest repair first. These pairs have ALREADY
  // passed the similarity test, so confidence is no longer what separates them
  // -- what separates them is how much each answer is worth, and that is the
  // smaller cluster's size. Ordering them by fewest-shared-photos instead
  // treated a pair of one-face strangers as the most urgent question in the
  // library: on the owner's index six of the first twenty slots went to pairs
  // worth one photo each, while person-16 (257 faces) split from person-745
  // (150 faces) ranked fourteenth. Shared photos stays as the tiebreak, since
  // one shared frame is far weaker evidence of two people than ten.
  //
  // The pairs BELOW their bar keep similarity first. There confidence IS the
  // binding constraint, and a wrong merge is unrecoverable, so a large tile is
  // a reason for care rather than for haste.
  found.sort(
    (x, y) =>
      Number(y.blockedByCoOccurrence) - Number(x.blockedByCoOccurrence) ||
      (x.blockedByCoOccurrence
        ? y.photosFixed - x.photosFixed || x.sharedAssets - y.sharedAssets
        : 0) ||
      y.similarity - x.similarity ||
      (x.a < y.a ? -1 : x.a > y.a ? 1 : x.b < y.b ? -1 : x.b > y.b ? 1 : 0),
  );
  return found.slice(0, limit);
}

export type { FaceObservation, Person } from "./types";
