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
  scale: number;
  /** Sum of quality weights behind `centroid`, which is a weighted mean. */
  weightSum: number;
  /** Capture-time span of this cluster; undefined when no face carried a time. */
  firstAt?: number;
  lastAt?: number;
};

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
    scale: centroidScale(person.centroid),
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
  const { assetIdSet: _assetIdSet, scale: _scale, ...record } = person;
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
export function extendFaceClusters(
  existing: Person[],
  observations: FaceObservation[],
  opts: ClusterOptions = {},
): Person[] {
  const identityThreshold = Number.isFinite(opts.threshold)
    ? (opts.threshold as number)
    : DEFAULT_IDENTITY_THRESHOLD;
  const perceptualThreshold = Number.isFinite(opts.perceptualThreshold)
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
  const identityMergeThreshold = Math.max(
    identityThreshold,
    Number.isFinite(opts.identityMergeThreshold)
      ? (opts.identityMergeThreshold as number)
      : DEFAULT_MERGE_THRESHOLD,
  );
  // Not clamped to `identityThreshold` -- see `evidencedMergeThreshold` above.
  // Defaults to the strict bar, so a caller that says nothing gets exactly
  // today's behaviour and this stays inert until someone opts in.
  const evidencedMergeThreshold = Math.min(
    identityMergeThreshold,
    Number.isFinite(opts.evidencedMergeThreshold)
      ? (opts.evidencedMergeThreshold as number)
      : identityMergeThreshold,
  );
  // Also uncapped by the assignment bar, for the same reason as the evidenced
  // bar: it governs two averages, not a face against a group.
  const temporalMergeThreshold = Math.min(
    evidencedMergeThreshold,
    Number.isFinite(opts.temporalMergeThreshold)
      ? (opts.temporalMergeThreshold as number)
      : evidencedMergeThreshold,
  );
  const people: MutablePerson[] = existing.map(mutablePerson);
  let nextPersonNumber = people.reduce((largest, person) => {
    const match = /^person-(\d+)$/u.exec(person.id);
    return match ? Math.max(largest, Number(match[1])) : largest;
  }, 0) + 1;

  for (const observation of observations) {
    const embedding = unitEmbedding(observation.embedding);
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

      // Average linkage: the mean cosine between this face and every face
      // already in the person, NOT the cosine against their mean. See
      // `centroidScale`.
      const similarity = cosine(embedding, person.centroid) * person.scale;
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
      const threshold =
        observation.embeddingKind === "identity"
          ? identityThreshold
          : perceptualThreshold;
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
        scale: centroidScale(embedding),
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
    person.scale = centroidScale(person.centroid);
    person.weightSum += weight;
    person.faceCount += 1;
    widenSpan(person, observation.capturedAt);
    if (!person.assetIdSet.has(observation.assetId)) {
      person.assetIdSet.add(observation.assetId);
      person.assetIds.push(observation.assetId);
    }
    opts.onAssign?.(observation, person.id);
  }

  mergeSimilarPeople(
    people,
    identityMergeThreshold,
    perceptualThreshold,
    evidencedMergeThreshold,
    temporalMergeThreshold,
    opts.constraints ?? [],
    opts.onMerge,
  );

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
 * ponytail: O(k^2) per merge round over k people; fine for the hundreds of
 * people a personal library yields. Swap for union-find on an ANN index if k
 * ever reaches thousands.
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
    cosine(a.centroid, b.centroid) * a.scale * b.scale;
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

  for (;;) {
    let bestI = -1;
    let bestJ = -1;
    let bestSimilarity = Number.NEGATIVE_INFINITY;

    for (let i = 0; i < people.length; i += 1) {
      for (let j = i + 1; j < people.length; j += 1) {
        const a = people[i];
        const b = people[j];
        if (!comparable(a, b)) continue;
        // Two clusters that each carry real evidence are judged on the bar
        // measured for AVERAGES; anything smaller is judged on the strict bar,
        // which is the one that behaves like a single pair.
        const evidenced =
          a.faceCount >= MERGE_EVIDENCE_MIN_FACES &&
          b.faceCount >= MERGE_EVIDENCE_MIN_FACES;
        // Clusters that overlap or nearly touch in time get the temporal bar,
        // which is how an infant's months chain together: each neighbouring
        // pair clears it, the union spans wider, and the next neighbour comes
        // into range on the following iteration. Requires BOTH to carry real
        // evidence, so a stray two-face cluster cannot ride a date into
        // somebody else.
        const gap = spanGap(a, b);
        const nearInTime =
          gap !== undefined && gap <= TEMPORAL_MERGE_WINDOW_MS;
        const identityBar = evidenced
          ? nearInTime
            ? Math.min(evidencedMergeThreshold, temporalMergeThreshold)
            : evidencedMergeThreshold
          : identityMergeThreshold;
        const threshold =
          a.embeddingKind === "identity" ? identityBar : perceptualThreshold;
        const similarity = linkage(a, b);
        if (similarity < threshold || similarity < bestSimilarity) {
          continue;
        }
        // Exact ties are common in synthetic and symmetric libraries, and
        // "whichever the loops reached first" makes the result depend on the
        // order people were discovered in — the one thing closest-first
        // merging exists to remove. Break them on the id pair instead.
        if (
          similarity === bestSimilarity &&
          bestI !== -1 &&
          pairKey(a, b) >= pairKey(people[bestI], people[bestJ])
        ) {
          continue;
        }
        // `blocked` and `origins` are both unions over the same pre-merge
        // clusters, so this single test is symmetric in i and j.
        if (intersects(blocked[i], origins[j])) continue;
        bestSimilarity = similarity;
        bestI = i;
        bestJ = j;
      }
    }

    if (bestI === -1) {
      return;
    }

    absorb(people, origins, blocked, bestI, bestJ, onMerge);
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
  survivor.scale = centroidScale(survivor.centroid);
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
  for (const origin of origins[dropIndex]) origins[keepIndex].add(origin);
  for (const origin of blocked[dropIndex]) blocked[keepIndex].add(origin);
  onMerge?.(absorbed.id, survivor.id);
  people.splice(dropIndex, 1);
  origins.splice(dropIndex, 1);
  blocked.splice(dropIndex, 1);
}

export type { FaceObservation, Person } from "./types";
