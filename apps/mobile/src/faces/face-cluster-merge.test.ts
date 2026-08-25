// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { DEFAULT_IDENTITY_THRESHOLD, DEFAULT_MERGE_THRESHOLD, SAME_PHOTO_EXCEPTION_SIMILARITY, extendFaceClusters } from "./face-cluster.ts";

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
// CHANGED (face-index v20): this used to assert that two disjoint centroids at
// cosine 0.40 merge when the caller asks for a 0.38 merge bar. That bar was
// calibrated for unaligned crops and, once 5-point alignment tightened the
// space, it was below the 0.62 bar a single face has to clear to join a person
// at all. Merging is the transitive step, so it can never be the easier one:
// face-cluster-recovery.test.ts measures eight clean identities collapsing into
// six tiles at 0.37 and into a single 112-face tile at 0.30. The merge bar is
// now clamped up to the assignment bar unconditionally, so an explicit 0.38 is
// raised to 0.62 and a 0.40 pair stays split.
// Positioned relative to the bars rather than written as literals, so the
// w600k_mbf swap (and the next one) cannot silently invert what this asserts.
const BELOW_IDENTITY = Number((DEFAULT_IDENTITY_THRESHOLD - 0.02).toFixed(4));
const LOW_MERGE_REQUEST = Number((DEFAULT_IDENTITY_THRESHOLD - 0.03).toFixed(4));
const splitIdentity = extendFaceClusters(
  [],
  [obs("portrait-a", 1, 0), { ...obs("portrait-b", 0, 0), embedding: atCosine(BELOW_IDENTITY) }],
  { identityMergeThreshold: LOW_MERGE_REQUEST },
);
assert(
  splitIdentity.length === 2,
  "an explicit merge bar below the identity bar is clamped up, so a sub-bar pair stays split",
);

// The pass still does its job: an order-dependent split of ONE person leaves
// centroids far closer than any two real people, and those still rejoin.
const rejoinedIdentity = extendFaceClusters(
  [],
  [obs("portrait-a", 1, 0), { ...obs("portrait-b", 0, 0), embedding: atCosine(0.9) }],
  { identityMergeThreshold: 0.38 },
);
assert(rejoinedIdentity.length === 1, "one person's split centroids at 0.90 still rejoin");

const cooccurring = extendFaceClusters(
  [],
  [
    obs("group-photo", 1, 0),
    {
      ...obs("group-photo", 0, 0),
      embedding: atCosine(SAME_PHOTO_EXCEPTION_SIMILARITY - 0.05),
    },
  ],
  { identityMergeThreshold: LOW_MERGE_REQUEST },
);
assert(cooccurring.length === 2, "ordinary co-faces stay a strict cannot-link");

const mirroredDuplicate = extendFaceClusters(
  [],
  [
    obs("panorama", 1, 0),
    {
      ...obs("panorama", 0, 0),
      embedding: atCosine(SAME_PHOTO_EXCEPTION_SIMILARITY + 0.05),
    },
  ],
  { identityMergeThreshold: LOW_MERGE_REQUEST },
);
assert(mirroredDuplicate.length === 1, "above the exception a mirror/panorama duplicate is permitted");

const collisionFree = extendFaceClusters(
  [
    { id: "person-1", faceCount: 1, assetIds: ["old-a"], centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-3", faceCount: 1, assetIds: ["old-b"], centroid: [0, 1], embeddingKind: "identity" },
  ],
  [obs("new", -1, 0)],
);
assert(collisionFree.some((person) => person.id === "person-4"), "new ids advance past persisted gaps");
assert(new Set(collisionFree.map((person) => person.id)).size === collisionFree.length, "person ids stay unique");

// Drift geometry, derived from the live bars.
const cosineFor = (degrees: number) => Math.cos((degrees * Math.PI) / 180);
const degreesFor = (cosine: number) => (Math.acos(cosine) * 180) / Math.PI;
const identityDegrees = degreesFor(DEFAULT_IDENTITY_THRESHOLD);
const secondDegrees = identityDegrees * 1.02;
const thirdDegrees = identityDegrees * 0.45;
// An explicit merge bar just above the assignment bar. Solving this against the
// DEFAULT merge bar leaves a margin under 0.006 in w600k_mbf space, because the
// port widened the assignment-to-merge gap from ~8 degrees to ~14; a fixture
// that tight tests floating point, not clustering. The clamping of a merge bar
// BELOW the assignment bar is already covered by `splitIdentity` above.
const DRIFT_MERGE_BAR = Number((DEFAULT_IDENTITY_THRESHOLD + 0.05).toFixed(4));
// Merging compares clusters by AVERAGE LINKAGE, so the far member drags the
// score down; the setup has to clear the bar on the mean, not on the centroid.
const driftLinkage =
  (cosineFor(secondDegrees) + cosineFor(secondDegrees - thirdDegrees)) / 2;
assert(
  cosineFor(secondDegrees) < DEFAULT_IDENTITY_THRESHOLD,
  "setup: second must seed its OWN cluster, just outside the assignment bar",
);
assert(
  cosineFor(thirdDegrees) > DEFAULT_IDENTITY_THRESHOLD &&
    thirdDegrees < secondDegrees / 2,
  "setup: third must join first, and be nearer first than second",
);
assert(
  driftLinkage > DRIFT_MERGE_BAR,
  `setup: average linkage must clear the merge bar (got ${driftLinkage.toFixed(4)})`,
);
assert(
  DRIFT_MERGE_BAR > DEFAULT_IDENTITY_THRESHOLD,
  "setup: the merge bar must not be clamped, or this tests clamping instead",
);

const assigned = new Map<string, string>();
const absorbed = new Map<string, string>();
extendFaceClusters(
  [],
  // With the merge bar clamped to the assignment bar, two freshly seeded
  // singletons can never merge — whatever kept them apart online keeps them
  // apart in the merge pass. The pass earns its keep only through centroid
  // DRIFT, which is the case it was written for: "second" seeds its own cluster
  // just outside the assignment bar, then "third" joins "first" and swings that
  // centroid halfway toward itself, bringing "second" inside the merge bar.
  //
  // The angles are derived from the bars so a model swap re-derives the setup
  // instead of inverting it; the three assertions below fail loudly if it no
  // longer holds.
  [
    obs("first", 1, 0),
    { ...obs("second", 0, 0), embedding: atCosine(cosineFor(secondDegrees)) },
    { ...obs("third", 0, 0), embedding: atCosine(cosineFor(thirdDegrees)) },
  ],
  {
    identityMergeThreshold: DRIFT_MERGE_BAR,
    onAssign: (observation, personId) => assigned.set(observation.assetId, personId),
    onMerge: (absorbedId, survivingId) => absorbed.set(absorbedId, survivingId),
  },
);
assert(assigned.size === 3, "every observation reports its exact online assignment");
assert(absorbed.size === 1, "post-assignment cluster aliases are reported");

// CHANGED (face-index v20): this used to assert that two 10-face clusters at
// cosine 0.32 merge under a lower "large cluster" floor of 0.30. That rule is
// deleted, and this now pins its inverse. Rewarding a cluster for having
// already absorbed 10 faces is a runaway-absorption engine: every merge drags a
// centroid toward the population mean, which RAISES its cosine against every
// cluster it has not eaten yet, which qualifies the next merge. Measured in
// face-cluster-recovery.test.ts, that path alone turns eight cleanly separated
// identities into one 112-face tile even when the base bar is correct — it is
// the 2,164-photo tile the beta device reported. One threshold governs, and
// well-supported clusters get no discount.
const supportedSplit = extendFaceClusters(
  [
    { id: "person-1", faceCount: 10, assetIds: ["set-a"], centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-2", faceCount: 10, assetIds: ["set-b"], centroid: atCosine(0.32), embeddingKind: "identity" },
  ],
  [],
  { identityMergeThreshold: 0.37 },
);
assert(supportedSplit.length === 2, "two well-supported clusters get no merge discount for being large");

const noisyOverlap = extendFaceClusters(
  [
    { id: "person-1", faceCount: 20, assetIds: ["shared", ...Array.from({ length: 19 }, (_, i) => `a-${i}`)], centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-2", faceCount: 20, assetIds: ["shared", ...Array.from({ length: 19 }, (_, i) => `b-${i}`)], centroid: atCosine(SAME_PHOTO_EXCEPTION_SIMILARITY - 0.05), embeddingKind: "identity" },
  ],
  [],
  { identityMergeThreshold: LOW_MERGE_REQUEST },
);
assert(noisyOverlap.length === 2, "same-photo clusters below the exception cannot merge");

const frequentOverlap = extendFaceClusters(
  [
    { id: "person-1", faceCount: 10, assetIds: ["shared-1", "shared-2", ...Array.from({ length: 8 }, (_, i) => `a-${i}`)], centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-2", faceCount: 10, assetIds: ["shared-1", "shared-2", ...Array.from({ length: 8 }, (_, i) => `b-${i}`)], centroid: atCosine(0.65), embeddingKind: "identity" },
  ],
  [],
  { identityMergeThreshold: 0.37 },
);
assert(frequentOverlap.length === 2, "lower-similarity co-faces remain a hard cannot-link");

const sparseSatellite = extendFaceClusters(
  [
    { id: "person-1", faceCount: 12, assetIds: Array.from({ length: 12 }, (_, i) => `anchor-${i}`), centroid: [1, 0], embeddingKind: "identity" },
    { id: "person-2", faceCount: 4, assetIds: Array.from({ length: 4 }, (_, i) => `anchor-${i}`), centroid: atCosine(0.86), embeddingKind: "identity" },
  ],
  [],
  { identityMergeThreshold: 0.37 },
);
assert(sparseSatellite.length === 1, "an extremely close overlapping satellite rejoins its anchor");

const assignableOnly = extendFaceClusters(
  [],
  [{ ...obs("profile", 1, 0), seedable: false }],
);
assert(assignableOnly.length === 0, "an assignable profile cannot seed a tile");
const assignedProfile = extendFaceClusters(
  [{ id: "person-1", faceCount: 1, assetIds: ["seed"], centroid: [1, 0], embeddingKind: "identity" }],
  [{ ...obs("profile", 1, 0.02), seedable: false }],
);
assert(assignedProfile[0]?.faceCount === 2, "an assignable profile can join a seeded person");

// eslint-disable-next-line no-console
console.log("face-cluster merge self-check passed");
