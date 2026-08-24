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

/**
 * Greedy online clustering against each existing centroid.
 *
 * This is O(n * k * d), where k is the number of people and d is the embedding
 * size. Brute-force centroid search is appropriate for tens of thousands of
 * faces; beyond that ceiling this should be replaced by an approximate index.
 */
export function clusterFaces(
  observations: FaceObservation[],
  opts: { threshold?: number; perceptualThreshold?: number } = {},
): Person[] {
  const identityThreshold = Number.isFinite(opts.threshold)
    ? (opts.threshold as number)
    : DEFAULT_IDENTITY_THRESHOLD;
  const perceptualThreshold = Number.isFinite(opts.perceptualThreshold)
    ? (opts.perceptualThreshold as number)
    : DEFAULT_PERCEPTUAL_THRESHOLD;
  const people: MutablePerson[] = [];

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

  return people.map(({ assetIdSet: _assetIdSet, ...person }) => person);
}

export type { FaceObservation, Person } from "./types";
