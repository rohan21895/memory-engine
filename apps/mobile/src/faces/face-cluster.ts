import type { FaceObservation, Person } from "./types";

export const DEFAULT_IDENTITY_THRESHOLD = 0.5;
export const DEFAULT_PERCEPTUAL_THRESHOLD = 0.92;

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
  const people: MutablePerson[] = existing.map((person) => ({
    ...person,
    assetIds: person.assetIds.slice(),
    centroid: person.centroid.slice(),
    assetIdSet: new Set(person.assetIds),
  }));

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
      people.push({
        id: `person-${people.length + 1}`,
        faceCount: 1,
        assetIds: [observation.assetId],
        centroid: observation.embedding.slice(),
        embeddingKind: observation.embeddingKind,
        assetIdSet: new Set([observation.assetId]),
      });
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
  }

  mergeSimilarPeople(people, identityThreshold, perceptualThreshold);

  return people.map(({ assetIdSet: _assetIdSet, ...person }) => person);
}

/**
 * Second pass: greedy online assignment is order-dependent, so one person's
 * faces routinely seed several clusters (a bad-angle first frame the later good
 * frames never match). Repeatedly merge the closest pair of same-kind people
 * whose centroids sit above the same similarity bar the assignment used, until
 * none qualify. Only collapses clusters already within threshold, so it never
 * merges people the assignment would have kept apart. The older cluster (lower
 * index) survives, keeping surfaced ids stable.
 *
 * ponytail: O(k^2) per merge round over k people; fine for the hundreds of
 * people a personal library yields. Swap for union-find on an ANN index if k
 * ever reaches thousands.
 */
function mergeSimilarPeople(
  people: MutablePerson[],
  identityThreshold: number,
  perceptualThreshold: number,
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
          a.embeddingKind === "identity" ? identityThreshold : perceptualThreshold;
        const similarity = cosine(a.centroid, b.centroid);
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
    people.splice(bestJ, 1);
  }
}

export type { FaceObservation, Person } from "./types";
