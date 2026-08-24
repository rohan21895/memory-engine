// @ts-expect-error Node's TypeScript runner requires the source extension.
import { clusterFaces, extendFaceClusters } from "./face-cluster.ts";

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
