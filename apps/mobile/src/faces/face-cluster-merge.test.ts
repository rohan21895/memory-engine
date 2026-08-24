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

// eslint-disable-next-line no-console
console.log("face-cluster merge self-check passed");
