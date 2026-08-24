// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { extendFaceClusters } from "./face-cluster.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-cluster merge self-check failed: ${message}`);
}

// Two near-identical identity centroids that greedy single-pass assignment
// splits into separate people (different first-seen frames) must collapse into
// one person after the agglomerative merge pass.
const obs = (assetId: string, x: number, y: number) => ({
  assetId,
  embedding: [x, y],
  embeddingKind: "identity" as const,
});

// Feed them so the second face seeds its own cluster (angle just below the
// 0.5 assignment bar against the first centroid), then the merge pass unifies.
const people = extendFaceClusters(
  [],
  [obs("a", 1, 0), obs("b", 0, 1), obs("c", 1, 0.02), obs("d", 0.02, 1)],
  { threshold: 0.5 },
);

assert(people.length === 2, `same person's clusters merged into two people (got ${people.length})`);
const counts = people.map((p) => p.faceCount).sort();
assert(counts[0] === 2 && counts[1] === 2, `both people keep their two faces (got ${counts.join(",")})`);

// Distinct people (orthogonal centroids, cosine 0) must NOT be merged.
const distinct = extendFaceClusters(
  [],
  [obs("a", 1, 0), obs("b", 0, 1)],
  { threshold: 0.5 },
);
assert(distinct.length === 2, "orthogonal identities stay separate");

const atCosine = (similarity: number) => [
  similarity,
  Math.sqrt(1 - similarity * similarity),
];
const splitIdentity = extendFaceClusters(
  [],
  [obs("portrait-a", 1, 0), { ...obs("portrait-b", 0, 0), embedding: atCosine(0.4) }],
  { identityMergeThreshold: 0.38 },
);
assert(splitIdentity.length === 1, "disjoint identity centroids at 0.40 merge in the calibrated pass");

const cooccurring = extendFaceClusters(
  [],
  [obs("group-photo", 1, 0), { ...obs("group-photo", 0, 0), embedding: atCosine(0.8) }],
  { identityMergeThreshold: 0.38 },
);
assert(cooccurring.length === 2, "two faces in one source photo are always a strict cannot-link");

const collisionFree = extendFaceClusters(
  [
    { id: "person-1", faceCount: 1, assetIds: ["old-a"], centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-3", faceCount: 1, assetIds: ["old-b"], centroid: [0, 1], embeddingKind: "identity" },
  ],
  [obs("new", -1, 0)],
);
assert(collisionFree.some((person) => person.id === "person-4"), "new ids advance past persisted gaps");
assert(new Set(collisionFree.map((person) => person.id)).size === collisionFree.length, "person ids stay unique");

const assigned = new Map<string, string>();
const absorbed = new Map<string, string>();
extendFaceClusters(
  [],
  [obs("first", 1, 0), { ...obs("second", 0, 0), embedding: atCosine(0.4) }],
  {
    identityMergeThreshold: 0.38,
    onAssign: (observation, personId) => assigned.set(observation.assetId, personId),
    onMerge: (absorbedId, survivingId) => absorbed.set(absorbedId, survivingId),
  },
);
assert(assigned.size === 2, "every observation reports its exact online assignment");
assert(absorbed.size === 1, "post-assignment cluster aliases are reported");

const supportedSplit = extendFaceClusters(
  [
    { id: "person-1", faceCount: 10, assetIds: ["set-a"], centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-2", faceCount: 10, assetIds: ["set-b"], centroid: atCosine(0.32), embeddingKind: "identity" },
  ],
  [],
  {
    identityLargeClusterMergeThreshold: 0.3,
    identityLargeClusterMinFaces: 10,
    identityMergeThreshold: 0.37,
  },
);
assert(supportedSplit.length === 1, "large disjoint splits use the calibrated lower merge floor");

const noisyOverlap = extendFaceClusters(
  [
    { id: "person-1", faceCount: 20, assetIds: ["shared", ...Array.from({ length: 19 }, (_, i) => `a-${i}`)], centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-2", faceCount: 20, assetIds: ["shared", ...Array.from({ length: 19 }, (_, i) => `b-${i}`)], centroid: atCosine(0.8), embeddingKind: "identity" },
  ],
  [],
  { identityLargeClusterMinFaces: 10, identityMergeThreshold: 0.37 },
);
assert(noisyOverlap.length === 1, "noisy co-detections cannot veto a high-confidence identity merge");

const frequentOverlap = extendFaceClusters(
  [
    { id: "person-1", faceCount: 10, assetIds: ["shared-1", "shared-2", ...Array.from({ length: 8 }, (_, i) => `a-${i}`)], centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-2", faceCount: 10, assetIds: ["shared-1", "shared-2", ...Array.from({ length: 8 }, (_, i) => `b-${i}`)], centroid: atCosine(0.65), embeddingKind: "identity" },
  ],
  [],
  { identityLargeClusterMinFaces: 10, identityMergeThreshold: 0.37 },
);
assert(frequentOverlap.length === 2, "lower-similarity co-faces remain a hard cannot-link");

const sparseSatellite = extendFaceClusters(
  [
    { id: "person-1", faceCount: 12, assetIds: Array.from({ length: 12 }, (_, i) => `anchor-${i}`), centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-2", faceCount: 4, assetIds: Array.from({ length: 4 }, (_, i) => `anchor-${i}`), centroid: atCosine(0.6), embeddingKind: "identity" },
  ],
  [],
  { identityLargeClusterMinFaces: 10, identityMergeThreshold: 0.37 },
);
assert(sparseSatellite.length === 1, "a supported fully-overlapping satellite rejoins its anchor");

// eslint-disable-next-line no-console
console.log("face-cluster merge self-check passed");
