// @ts-expect-error Node's TypeScript runner requires the source extension.
import { DEFAULT_IDENTITY_THRESHOLD, DEFAULT_MERGE_THRESHOLD, clusterFaces, extendFaceClusters } from "./face-cluster.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

{
  const people = clusterFaces([
    { assetId: "photo-a", embedding: [1, 0, 0], embeddingKind: "identity" },
    { assetId: "photo-b", embedding: [0.999, 0.01, 0], embeddingKind: "identity" },
  ]);
  assert(people.length === 1, "near-identical faces should form one person");
  assert(people[0].faceCount === 2, "both faces should be counted");
  assert(
    people[0].assetIds.join(",") === "photo-a,photo-b",
    "asset IDs should retain deterministic observation order",
  );
}

{
  const people = clusterFaces([
    { assetId: "photo-a", embedding: [1, 0], embeddingKind: "identity" },
    { assetId: "photo-b", embedding: [0, 1], embeddingKind: "identity" },
  ]);
  assert(people.length === 2, "orthogonal faces should form two people");
}

{
  const observations = [
    { assetId: "photo-a", embedding: [1, 0], embeddingKind: "identity" as const },
    { assetId: "photo-b", embedding: [0.8, 0.6], embeddingKind: "identity" as const },
  ];
  assert(
    clusterFaces(observations, { threshold: 0.7 }).length === 1,
    "a pair above the threshold should cluster",
  );
  assert(
    clusterFaces(observations, { threshold: 0.9 }).length === 2,
    "raising the threshold should produce smaller clusters",
  );
}

assert(clusterFaces([]).length === 0, "empty input should return an empty array");

{
  const firstBatch = clusterFaces([
    { assetId: "photo-a", embedding: [1, 0], embeddingKind: "identity" },
  ]);
  const people = extendFaceClusters(firstBatch, [
    { assetId: "photo-b", embedding: [0.9, 0.1], embeddingKind: "identity" },
    { assetId: "photo-c", embedding: [0, 1], embeddingKind: "identity" },
  ]);
  assert(people.length === 2, "incremental batches should extend existing clusters");
  assert(people[0].id === "person-1", "existing person ids must remain stable");
  assert(people[0].faceCount === 2, "the incremental match updates the centroid cluster");
  assert(people[1].id === "person-2", "new people receive deterministic ids");
}

{
  const people = clusterFaces([
    { assetId: "photo-a", embedding: [1, 0], embeddingKind: "identity" },
    { assetId: "photo-b", embedding: [1, 0, 0], embeddingKind: "identity" },
  ]);
  assert(
    people.length === 2,
    "mismatched embedding lengths should be treated as dissimilar",
  );
}

{
  const people = clusterFaces([
    { assetId: "identity", embedding: [1, 0], embeddingKind: "identity" },
    { assetId: "fallback", embedding: [1, 0], embeddingKind: "perceptual" },
  ]);
  assert(
    people.length === 2,
    "identity and perceptual spaces must never be mixed into one person",
  );
}

// The merge bar is the transitive step and must be the tightest number in the
// system. It is clamped up to the assignment bar UNCONDITIONALLY, including on
// the explicit-caller path: the guard used to sit only on the default branch,
// so face-index.ts passing an explicit 0.37 bypassed it entirely and a
// recalibration of the default to 0.72 had no effect on the only path that
// ships. A caller asking for a looser bar is asking for a bug.
{
  assert(
    DEFAULT_MERGE_THRESHOLD >= DEFAULT_IDENTITY_THRESHOLD,
    "the default merge bar is at least the default assignment bar",
  );

  const atCosine = (similarity: number) => [
    similarity,
    Math.sqrt(1 - similarity * similarity),
  ];
  // 0.50 clears the requested 0.45 merge bar but not the 0.62 assignment bar,
  // so the clamp is the only thing standing between these two and one tile.
  const clamped = clusterFaces(
    [
      { assetId: "a", embedding: [1, 0], embeddingKind: "identity" },
      { assetId: "b", embedding: atCosine(0.5), embeddingKind: "identity" },
    ],
    { identityMergeThreshold: 0.45 },
  );
  assert(
    clamped.length === 2,
    "an explicit merge threshold below the identity threshold is clamped up",
  );

  // The clamp only ever raises: an explicit bar ABOVE assignment is obeyed.
  const honoured = clusterFaces(
    [
      { assetId: "a", embedding: [1, 0], embeddingKind: "identity" },
      { assetId: "b", embedding: atCosine(0.7), embeddingKind: "identity" },
    ],
    { identityMergeThreshold: 0.95, threshold: 0.9 },
  );
  assert(
    honoured.length === 2,
    "an explicit merge threshold above the identity threshold is honoured",
  );
}

{
  const fallback = [
    { assetId: "base", embedding: [1, 0], embeddingKind: "perceptual" as const },
    { assetId: "near", embedding: [0.94, 0.341], embeddingKind: "perceptual" as const },
  ];
  assert(
    clusterFaces(fallback).length === 1,
    "the fallback should retain its stricter perceptual calibration",
  );
  assert(
    clusterFaces([
      fallback[0],
      { assetId: "different", embedding: [0.8, 0.6], embeddingKind: "perceptual" },
    ]).length === 2,
    "the fallback should not use the looser identity threshold",
  );
}
