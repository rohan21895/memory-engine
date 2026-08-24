// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { SAME_PHOTO_EXCEPTION_SIMILARITY, clusterFaces, extendFaceClusters } from "./face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { faceQualityTier, scanFaceAssets } from "./face-index.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-cluster constraints self-check failed: ${message}`);
}

/** A unit vector whose cosine against [1, 0] is exactly `similarity`. */
const atCosine = (similarity: number) => [
  similarity,
  Math.sqrt(1 - similarity * similarity),
];
/** A unit vector on the 2-D circle, for reasoning about merges in degrees. */
const atDegrees = (degrees: number) => [
  Math.cos((degrees * Math.PI) / 180),
  Math.sin((degrees * Math.PI) / 180),
];

/** Order-insensitive fingerprint of a clustering: which assets landed together. */
const partition = (people: { assetIds: string[] }[]) =>
  people
    .map((person) => person.assetIds.slice().sort().join(","))
    .sort()
    .join(" | ");

assert(
  SAME_PHOTO_EXCEPTION_SIMILARITY === 0.85,
  "the mirror/panorama exception stays at cosine 0.85 (d_cos 0.15)",
);

// (a) Two faces in the SAME photo are two different people — a parent and their
// child, or two siblings, sit well above the 0.5 identity bar without being the
// same person. Below the exception they must never end up in one tile, on
// either the online-assignment path or the agglomerative merge path.
for (const similarity of [0.6, 0.8, 0.84]) {
  const online = clusterFaces(
    [
      { assetId: "group-shot", embedding: [1, 0], embeddingKind: "identity" },
      { assetId: "group-shot", embedding: atCosine(similarity), embeddingKind: "identity" },
    ],
    { identityMergeThreshold: 0.37, threshold: 0.5 },
  );
  assert(
    online.length === 2,
    `co-faces at cosine ${similarity} must not share a tile (got ${online.length})`,
  );

  const merged = extendFaceClusters(
    [
      { id: "person-1", faceCount: 6, assetIds: ["group-shot", "a-solo"], centroid: [1, 0], embeddingKind: "identity" },
      { id: "person-2", faceCount: 6, assetIds: ["group-shot", "b-solo"], centroid: atCosine(similarity), embeddingKind: "identity" },
    ],
    [],
    { identityMergeThreshold: 0.37 },
  );
  assert(
    merged.length === 2,
    `co-occurring clusters at cosine ${similarity} must not merge (got ${merged.length})`,
  );
}

// (b) Above the exception the same photo legitimately holds one face twice:
// mirrors, panorama stitches, collages, a photo of a photo. Both paths merge.
for (const similarity of [0.86, 0.95]) {
  const online = clusterFaces(
    [
      { assetId: "mirror", embedding: [1, 0], embeddingKind: "identity" },
      { assetId: "mirror", embedding: atCosine(similarity), embeddingKind: "identity" },
    ],
    { identityMergeThreshold: 0.37, threshold: 0.5 },
  );
  assert(
    online.length === 1 && online[0].faceCount === 2,
    `a mirrored face at cosine ${similarity} rejoins its person (got ${online.length})`,
  );

  const merged = extendFaceClusters(
    [
      { id: "person-1", faceCount: 6, assetIds: ["mirror", "a-solo"], centroid: [1, 0], embeddingKind: "identity" },
      { id: "person-2", faceCount: 6, assetIds: ["mirror", "b-solo"], centroid: atCosine(similarity), embeddingKind: "identity" },
    ],
    [],
    { identityMergeThreshold: 0.37 },
  );
  assert(
    merged.length === 1,
    `co-occurring clusters at cosine ${similarity} may merge (got ${merged.length})`,
  );
}

// (c) A tiny or extreme-yaw face is a bridge: it lands near every other bad
// face, so it must never reach the embedder, and an only-assignable face must
// never create a tile of its own.
const fullFrame = { id: "photo", width: 1000, height: 1000 };
assert(
  faceQualityTier(fullFrame, { x: 0, y: 0, width: 30, height: 30 }) === null,
  "a 30px face is below the assignable floor",
);
assert(
  faceQualityTier({ id: "wide", width: 4000, height: 3000 }, { x: 0, y: 0, width: 80, height: 80 }) === null,
  "an 80px face in a 3000px frame is too small a fraction to use",
);
assert(
  faceQualityTier(fullFrame, { x: 0, y: 0, width: 200, height: 200, headEulerAngleY: -60 }) === null,
  "a 60 degree profile has no alignable landmark set",
);
assert(
  faceQualityTier(fullFrame, { x: 0, y: 0, width: 48, height: 48, headEulerAngleY: 40 }) === "assignable",
  "a small angled face may still join an existing person",
);
assert(
  faceQualityTier(fullFrame, { x: 0, y: 0, width: 200, height: 200 }) === "seedable",
  "a large face with no reported yaw degrades to frontal rather than rejecting",
);
assert(
  faceQualityTier(fullFrame, { x: 0, y: 0, width: Number.NaN, height: 200 }) === null,
  "a non-finite box is discarded instead of slipping through the comparisons",
);

{
  let embedCalls = 0;
  const observations = await scanFaceAssets(
    [
      { id: "tiny", width: 1000, height: 1000 },
      { id: "profile", width: 1000, height: 1000 },
      { id: "angled", width: 1000, height: 1000 },
    ],
    {
      isDetectionAvailable: () => true,
      detectFaces: async (uri: string) => {
        if (uri.endsWith("/tiny")) return [{ x: 0, y: 0, width: 24, height: 24 }];
        if (uri.endsWith("/profile")) {
          return [{ x: 0, y: 0, width: 300, height: 300, headEulerAngleY: 72 }];
        }
        return [{ x: 0, y: 0, width: 48, height: 48, headEulerAngleY: 40 }];
      },
      embedFace: async () => {
        embedCalls += 1;
        return { embedding: [1, 0], kind: "identity" as const };
      },
    },
  );
  assert(embedCalls === 1, `only the assignable face is embedded (got ${embedCalls})`);
  assert(
    observations.length === 1 && observations[0].seedable === false,
    "the surviving face is marked assignable-only",
  );
  assert(
    clusterFaces(observations).length === 0,
    "a tiny/extreme-yaw library produces no person tiles at all",
  );
}

// (d) Determinism. Greedy online assignment is order-dependent by nature, so
// the merge pass has to take the CLOSEST qualifying pair rather than the first
// one it finds; otherwise the same library clusters differently between runs.
{
  const group = (prefix: string, base: number[], offsets: number[][]) =>
    offsets.map((offset, index) => ({
      assetId: `${prefix}-${index}`,
      embedding: base.map((value, axis) => value + offset[axis]),
      embeddingKind: "identity" as const,
    }));
  const observations = [
    ...group("ana", [1, 0, 0], [[0, 0, 0], [-0.001, 0.03, 0], [-0.002, 0, 0.05]]),
    ...group("ben", [0, 1, 0], [[0, 0, 0], [0.03, -0.001, 0], [0, -0.002, 0.05]]),
    ...group("cal", [0, 0, 1], [[0, 0, 0], [0.03, 0, -0.001], [0, 0.05, -0.002]]),
  ];
  const orders = [
    observations,
    observations.slice().reverse(),
    observations.map((_, index) => observations[(index + 4) % observations.length]),
    [0, 3, 6, 1, 4, 7, 2, 5, 8].map((index) => observations[index]),
    [8, 0, 5, 2, 7, 1, 4, 6, 3].map((index) => observations[index]),
  ];
  const signatures = orders.map((order) =>
    partition(clusterFaces(order, { identityMergeThreshold: 0.37, threshold: 0.5 })),
  );
  assert(
    signatures.every((signature) => signature === signatures[0]),
    `input order must not change the clustering:\n${signatures.join("\n")}`,
  );
  assert(
    signatures[0] === "ana-0,ana-1,ana-2 | ben-0,ben-1,ben-2 | cal-0,cal-1,cal-2",
    `three distinct people stay three people (got ${signatures[0]})`,
  );
}

// The same property for the merge pass, plus the chaining guard: a bridge
// cluster sitting between two people who co-occur in one photo may absorb one
// of them, but the merged asset set inherits the cannot-link, so it can never
// go on to swallow the other. A first-found merge would instead pair the bridge
// with whichever neighbour came first in the array.
{
  const ana = { id: "person-1", faceCount: 2, assetIds: ["group-shot", "ana-solo"], centroid: atDegrees(0), embeddingKind: "identity" as const };
  const bridge = { id: "person-2", faceCount: 2, assetIds: ["bridge-solo"], centroid: atDegrees(30), embeddingKind: "identity" as const };
  const cal = { id: "person-3", faceCount: 2, assetIds: ["group-shot", "cal-solo"], centroid: atDegrees(70), embeddingKind: "identity" as const };
  const orders = [
    [ana, bridge, cal],
    [ana, cal, bridge],
    [bridge, ana, cal],
    [bridge, cal, ana],
    [cal, ana, bridge],
    [cal, bridge, ana],
  ];
  const signatures = orders.map((order) =>
    partition(extendFaceClusters(order, [], { identityMergeThreshold: 0.5 })),
  );
  assert(
    signatures.every((signature) => signature === signatures[0]),
    `merge order must not change the outcome:\n${signatures.join("\n")}`,
  );
  assert(
    signatures[0] === "ana-solo,bridge-solo,group-shot | cal-solo,group-shot",
    `the bridge joins its closest neighbour only (got ${signatures[0]})`,
  );

  const stable = extendFaceClusters([ana, bridge, cal], [], { identityMergeThreshold: 0.5 });
  assert(
    stable.some((person) => person.id === "person-1") &&
      !stable.some((person) => person.id === "person-2"),
    "the older cluster survives a merge so surfaced ids stay stable",
  );
}

// eslint-disable-next-line no-console
console.log("face-cluster constraints self-check passed");
