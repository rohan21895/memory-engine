import type { FaceObservation, Person } from "./types";

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
 */
export const DEFAULT_IDENTITY_THRESHOLD = 0.62;

/**
 * Cluster-to-cluster merging is the transitive step, so it is tighter still.
 * A centroid is an average of many faces; two centroids being merely similar is
 * far weaker evidence than two faces being similar, and a single wrong merge is
 * unrecoverable for the user (two people permanently fused under one name).
 */
export const DEFAULT_MERGE_THRESHOLD = 0.72;

export const DEFAULT_PERCEPTUAL_THRESHOLD = 0.92;

/**
 * Same-photo cannot-link escape hatch.
 *
 * Two faces detected in ONE photo are almost never the same person, so a shared
 * asset id is a hard constraint: without it a parent absorbs their child and
 * siblings collapse into a single tile, which is the worst-looking failure in a
 * family library. The exception is a face that legitimately appears twice in a
 * single frame — mirrors, panorama stitches, collages, photos-of-photos — and
 * those land far above any identity bar. Cosine >= 0.85 (d_cos < 0.15) is that
 * exception; anything below stays split even when the identity threshold passes.
 */
export const SAME_PHOTO_EXCEPTION_SIMILARITY = 0.85;

/** True when two people draw on at least one common photo (a cannot-link). */
function sharesAsset(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  const [smaller, larger] = a.size <= b.size ? [a, b] : [b, a];
  for (const assetId of smaller) {
    if (larger.has(assetId)) return true;
  }
  return false;
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

export function updateCentroid(
  centroid: number[],
  embedding: number[],
  previousCount: number,
): number[] {
  if (
    centroid.length === 0 ||
    centroid.length !== embedding.length ||
    previousCount < 1
  ) {
    return centroid.slice();
  }

  const nextCount = previousCount + 1;
  return centroid.map(
    (value, index) =>
      (value * previousCount + embedding[index]) / nextCount,
  );
}

type MutablePerson = Person & { assetIdSet: Set<string> };

type ClusterOptions = {
  identityMergeThreshold?: number;
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
  const people: MutablePerson[] = existing.map((person) => ({
    ...person,
    assetIds: person.assetIds.slice(),
    centroid: person.centroid.slice(),
    assetIdSet: new Set(person.assetIds),
  }));
  let nextPersonNumber = people.reduce((largest, person) => {
    const match = /^person-(\d+)$/u.exec(person.id);
    return match ? Math.max(largest, Number(match[1])) : largest;
  }, 0) + 1;

  for (const observation of observations) {
    let bestIndex = -1;
    let bestSimilarity = Number.NEGATIVE_INFINITY;

    for (let index = 0; index < people.length; index += 1) {
      const person = people[index];
      if (
        observation.embeddingKind !== person.embeddingKind ||
        observation.embedding.length === 0 ||
        observation.embedding.length !== person.centroid.length
      ) {
        continue;
      }

      const similarity = cosine(observation.embedding, person.centroid);
      // Cannot-link: this person already owns a face from this very photo, so
      // joining would fuse two people who merely posed together. Only the
      // mirror/panorama exception may cross it.
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
        centroid: observation.embedding.slice(),
        embeddingKind: observation.embeddingKind,
        assetIdSet: new Set([observation.assetId]),
      });
      opts.onAssign?.(observation, id);
      continue;
    }

    const person = people[bestIndex];
    person.centroid = updateCentroid(
      person.centroid,
      observation.embedding,
      person.faceCount,
    );
    person.faceCount += 1;
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
    opts.onMerge,
  );

  return people.map(({ assetIdSet: _assetIdSet, ...person }) => person);
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
  onMerge?: (absorbedPersonId: string, survivingPersonId: string) => void,
): void {
  for (;;) {
    let bestI = -1;
    let bestJ = -1;
    let bestSimilarity = Number.NEGATIVE_INFINITY;

    for (let i = 0; i < people.length; i += 1) {
      for (let j = i + 1; j < people.length; j += 1) {
        const a = people[i];
        const b = people[j];
        if (
          a.embeddingKind !== b.embeddingKind ||
          a.centroid.length === 0 ||
          a.centroid.length !== b.centroid.length
        ) {
          continue;
        }
        const threshold =
          a.embeddingKind === "identity"
            ? identityMergeThreshold
            : perceptualThreshold;
        const similarity = cosine(a.centroid, b.centroid);
        if (similarity < threshold || similarity <= bestSimilarity) {
          continue;
        }
        // The union of both asset sets carries the constraint forward, so a
        // merged cluster inherits every cannot-link of its parts: a bad bridge
        // face can never chain two identities that co-occur in one photo.
        if (
          a.embeddingKind === "identity" &&
          similarity < SAME_PHOTO_EXCEPTION_SIMILARITY &&
          sharesAsset(a.assetIdSet, b.assetIdSet)
        ) {
          continue;
        }
        bestSimilarity = similarity;
        bestI = i;
        bestJ = j;
      }
    }

    if (bestI === -1) {
      return;
    }

    const survivor = people[bestI];
    const absorbed = people[bestJ];
    const total = survivor.faceCount + absorbed.faceCount;
    survivor.centroid = survivor.centroid.map(
      (value, index) =>
        (value * survivor.faceCount + absorbed.centroid[index] * absorbed.faceCount) /
        total,
    );
    survivor.faceCount = total;
    for (const assetId of absorbed.assetIds) {
      if (!survivor.assetIdSet.has(assetId)) {
        survivor.assetIdSet.add(assetId);
        survivor.assetIds.push(assetId);
      }
    }
    onMerge?.(absorbed.id, survivor.id);
    people.splice(bestJ, 1);
  }
}

export type { FaceObservation, Person } from "./types";
