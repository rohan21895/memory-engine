import type { FaceObservation, Person } from "./types";

export const DEFAULT_IDENTITY_THRESHOLD = 0.5;
export const DEFAULT_PERCEPTUAL_THRESHOLD = 0.92;
const OVERLAP_TOLERANT_IDENTITY_SIMILARITY = 0.66;
const SPARSE_DUPLICATE_SIMILARITY = 0.55;
const SPARSE_DUPLICATE_OVERLAP_RATIO = 0.9;
const SPARSE_DUPLICATE_MIN_FACES = 2;
const SPARSE_DUPLICATE_MAX_FACES = 8;

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
  identityLargeClusterMergeThreshold?: number;
  identityLargeClusterMinFaces?: number;
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
  const identityMergeThreshold = Number.isFinite(opts.identityMergeThreshold)
    ? (opts.identityMergeThreshold as number)
    : identityThreshold;
  const identityLargeClusterMergeThreshold = Number.isFinite(
    opts.identityLargeClusterMergeThreshold,
  )
    ? (opts.identityLargeClusterMergeThreshold as number)
    : identityMergeThreshold;
  const identityLargeClusterMinFaces = Number.isFinite(
    opts.identityLargeClusterMinFaces,
  )
    ? (opts.identityLargeClusterMinFaces as number)
    : Number.POSITIVE_INFINITY;
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
        person.assetIdSet.has(observation.assetId) ||
        observation.embedding.length === 0 ||
        observation.embedding.length !== person.centroid.length
      ) {
        continue;
      }

      const similarity = cosine(observation.embedding, person.centroid);
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
    identityLargeClusterMergeThreshold,
    identityLargeClusterMinFaces,
    perceptualThreshold,
    opts.onMerge,
  );

  return people.map(({ assetIdSet: _assetIdSet, ...person }) => person);
}

/**
 * Second pass: greedy online assignment is order-dependent, so one person's
 * faces routinely seed several clusters (a bad-angle first frame the later good
 * frames never match). Repeatedly merge the closest pair of same-kind people
 * above a calibrated centroid bar. Co-occurrence remains a cannot-link except
 * for device-calibrated, well-supported duplicate signatures. The older
 * cluster (lower index) survives, keeping surfaced ids stable.
 *
 * ponytail: O(k^2) per merge round over k people; fine for the hundreds of
 * people a personal library yields. Swap for union-find on an ANN index if k
 * ever reaches thousands.
 */
function mergeSimilarPeople(
  people: MutablePerson[],
  identityMergeThreshold: number,
  identityLargeClusterMergeThreshold: number,
  identityLargeClusterMinFaces: number,
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
        const sharedAssetCount = a.assetIds.reduce(
          (count, assetId) => count + Number(b.assetIdSet.has(assetId)),
          0,
        );
        const largeIdentityPair =
          a.embeddingKind === "identity" &&
          a.faceCount >= identityLargeClusterMinFaces &&
          b.faceCount >= identityLargeClusterMinFaces;
        const threshold = a.embeddingKind === "identity"
          ? largeIdentityPair
            ? identityLargeClusterMergeThreshold
            : identityMergeThreshold
          : perceptualThreshold;
        const similarity = cosine(a.centroid, b.centroid);
        const smallerFaceCount = Math.min(a.faceCount, b.faceCount);
        const largerFaceCount = Math.max(a.faceCount, b.faceCount);
        const overlapRatio =
          sharedAssetCount /
          Math.max(1, Math.min(a.assetIdSet.size, b.assetIdSet.size));
        const supportedOverlapDuplicate =
          similarity >= OVERLAP_TOLERANT_IDENTITY_SIMILARITY &&
          a.faceCount >= identityLargeClusterMinFaces &&
          b.faceCount >= identityLargeClusterMinFaces;
        const sparseDuplicate =
          similarity >= SPARSE_DUPLICATE_SIMILARITY &&
          overlapRatio >= SPARSE_DUPLICATE_OVERLAP_RATIO &&
          smallerFaceCount >= SPARSE_DUPLICATE_MIN_FACES &&
          smallerFaceCount <= SPARSE_DUPLICATE_MAX_FACES &&
          largerFaceCount >= identityLargeClusterMinFaces;
        if (
          a.embeddingKind === "identity" &&
          sharedAssetCount > 0 &&
          !supportedOverlapDuplicate &&
          !sparseDuplicate
        ) {
          continue;
        }
        if (similarity >= threshold && similarity > bestSimilarity) {
          bestSimilarity = similarity;
          bestI = i;
          bestJ = j;
        }
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
