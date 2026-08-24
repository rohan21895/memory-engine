// @ts-expect-error Node's TypeScript runner requires the source extension.
import { clusterFaces } from "./face-cluster.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

{
  const people = clusterFaces([
    { assetId: "photo-a", embedding: [1, 0, 0] },
    { assetId: "photo-b", embedding: [0.999, 0.01, 0] },
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
    { assetId: "photo-a", embedding: [1, 0] },
    { assetId: "photo-b", embedding: [0, 1] },
  ]);
  assert(people.length === 2, "orthogonal faces should form two people");
}

{
  const observations = [
    { assetId: "photo-a", embedding: [1, 0] },
    { assetId: "photo-b", embedding: [0.8, 0.6] },
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
  const people = clusterFaces([
    { assetId: "photo-a", embedding: [1, 0] },
    { assetId: "photo-b", embedding: [1, 0, 0] },
  ]);
  assert(
    people.length === 2,
    "mismatched embedding lengths should be treated as dissimilar",
  );
}
