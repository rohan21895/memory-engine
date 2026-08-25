// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { DEFAULT_IDENTITY_THRESHOLD, DEFAULT_MERGE_THRESHOLD, SAME_PHOTO_EXCEPTION_SIMILARITY, clusterFaces, extendFaceClusters } from "./face-cluster.ts";
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

// Ported with the w600k_mbf swap, holding its old offset above the merge bar.
// The value matters less than the ordering asserted below it: the exception has
// to sit ABOVE the merge bar, or a same-photo pair would merge through the
// normal path and the cannot-link would be decorative.
assert(
  SAME_PHOTO_EXCEPTION_SIMILARITY === 0.72,
  "the mirror/panorama exception is the w600k_mbf-space bar",
);
assert(
  SAME_PHOTO_EXCEPTION_SIMILARITY > DEFAULT_MERGE_THRESHOLD,
  "the same-photo exception must be stricter than an ordinary merge",
);

// (a) Two faces in the SAME photo are two different people — a parent and their
// child, or two siblings, sit well above the 0.5 identity bar without being the
// same person. Below the exception they must never end up in one tile, on
// either the online-assignment path or the agglomerative merge path.
// Derived from the bar, not written as literals: these moved from 0.85-space to
// 0.72-space with the w600k_mbf swap, and literals would have quietly started
// testing the opposite case.
const BELOW_EXCEPTION = [0.15, 0.5, 0.9].map(
  (fraction) => Number((SAME_PHOTO_EXCEPTION_SIMILARITY * fraction).toFixed(3)),
);
const ABOVE_EXCEPTION = [
  Number((SAME_PHOTO_EXCEPTION_SIMILARITY + 0.02).toFixed(3)),
  0.95,
];
for (const similarity of BELOW_EXCEPTION) {
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
for (const similarity of ABOVE_EXCEPTION) {
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

// (e) THE CANNOT-LINK MUST BE TRANSITIVE.
//
// Blocking only the DIRECT link between two co-occurring clusters is not a
// constraint, it is a speed bump: a merge with an unrelated third cluster moves
// a centroid, and the exception was re-measured against the moved centroid. So
// a pair that was forbidden became legal one round later, with no new evidence
// about either person. Concretely, and this is the shape the device produced:
//
//   ana    at   0 deg, photos {group-shot, ana-solo}
//   bridge at  16 deg, photos {bridge-solo}
//   cal    at  38 deg, photos {group-shot, cal-solo}
//
// ana-cal sit at cosine 0.788 and share group-shot, so they are a cannot-link
// and can never merge directly. ana-bridge at 0.961 merges, which swings ana's
// centroid to 8 deg, and from there cal is at 0.866 — over the 0.85 exception.
// The forbidden pair merges via the chain. The relation is now frozen before the
// first merge and inherited on every merge, so drift cannot dissolve it.
{
  const withPhotos = (id: string, degrees: number, assetIds: string[]) => ({
    id,
    faceCount: 1,
    assetIds,
    centroid: atDegrees(degrees),
    embeddingKind: "identity" as const,
  });
  // The angles are derived from the exception rather than written down, so the
  // scenario still sets itself up after a model swap moves the bar. The three
  // setup assertions are the point: if any stops holding, this test is no longer
  // testing chaining and would pass for the wrong reason.
  const barDegrees = (Math.acos(SAME_PHOTO_EXCEPTION_SIMILARITY) * 180) / Math.PI;
  const calDegrees = barDegrees * 1.15;
  const bridgeDegrees = barDegrees * 0.45;
  const cosOf = (degrees: number) => Math.cos((degrees * Math.PI) / 180);
  assert(
    cosOf(calDegrees) < SAME_PHOTO_EXCEPTION_SIMILARITY,
    "setup: the forbidden pair starts BELOW the exception",
  );
  assert(
    cosOf(bridgeDegrees) > SAME_PHOTO_EXCEPTION_SIMILARITY,
    "setup: the bridge is close enough to ana to merge",
  );
  assert(
    cosOf(calDegrees - bridgeDegrees / 2) > SAME_PHOTO_EXCEPTION_SIMILARITY,
    "setup: after the bridge moves ana's centroid, cal clears the exception",
  );
  const chained = extendFaceClusters(
    [
      withPhotos("person-1", 0, ["group-shot", "ana-solo"]),
      withPhotos("person-2", bridgeDegrees, ["bridge-solo"]),
      withPhotos("person-3", calDegrees, ["group-shot", "cal-solo"]),
    ],
    [],
    {
      identityMergeThreshold: SAME_PHOTO_EXCEPTION_SIMILARITY,
      threshold: SAME_PHOTO_EXCEPTION_SIMILARITY,
    },
  );
  const fused = chained.filter((person) => person.assetIds.includes("group-shot"));
  assert(
    fused.length === 2,
    `co-occurring identities must not be chained through a bridge (got ${partition(chained)})`,
  );
  assert(
    chained.length === 2,
    `the bridge itself still joins its closest neighbour (got ${partition(chained)})`,
  );
}

// (f) THE SAME PROPERTY END TO END, ON EMBEDDINGS AS BAD AS THE DEVICE'S.
//
// The 0.724 impostor median this was originally built around came from the
// mirrored-alignment bug, not from the world: once alignment was fixed the same
// library measured 0.177, and in w600k_mbf space it is nearer 0.004. So the
// generator is now pinned to the BAR rather than to that dead number — impostors
// sit adversarially close, above the assignment bar, but still below the
// same-photo exception, because above it the clusterer is *supposed* to fuse
// them as a mirror or collage and the invariant genuinely does not apply.
//
// The invariant itself is unchanged: a tile may never hold two faces out of one
// photo. It stays checkable on the phone — a 10,851-face tile spanning 5,979
// photos is 1.8 faces per photo, and that is proof of a broken constraint rather
// than proof of a popular person.
//
// The generator is the recovery-suite one recalibrated to the device: every face
// is A*shared + B*identity + C*noise, re-normalized, giving impostor cosine A^2
// and genuine cosine A^2+B^2. Four people appear in every photo.
{
  const DIMENSIONS = 96;
  let state = 20260825 >>> 0;
  const random = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
  const gaussian = () =>
    Math.sqrt(-2 * Math.log(Math.max(random(), Number.EPSILON))) *
    Math.cos(2 * Math.PI * random());
  const normalize = (values: number[]) => {
    const length = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
    return values.map((value) => value / length);
  };
  const unitVector = () => normalize(Array.from({ length: DIMENSIONS }, gaussian));
  const mix = (parts: Array<[number, number[]]>) =>
    normalize(
      Array.from({ length: DIMENSIONS }, (_unused, axis) =>
        parts.reduce((sum, [weight, vector]) => sum + weight * vector[axis], 0),
      ),
    );

  // Just under the exception: as hard as this test can be while the constraint
  // it checks still applies at all.
  const IMPOSTOR = Number((SAME_PHOTO_EXCEPTION_SIMILARITY - 0.05).toFixed(3));
  const GENUINE = Number(((1 + IMPOSTOR) / 2).toFixed(3));
  const shared = unitVector();
  const identityCount = 4;
  const photoCount = 25;
  const identities = Array.from({ length: identityCount }, unitVector);
  const faces: {
    assetId: string;
    embedding: number[];
    embeddingKind: "identity";
  }[] = [];
  for (let photo = 0; photo < photoCount; photo += 1) {
    for (let identity = 0; identity < identityCount; identity += 1) {
      faces.push({
        assetId: `family-${photo}`,
        embedding: mix([
          [Math.sqrt(IMPOSTOR), shared],
          [Math.sqrt(GENUINE - IMPOSTOR), identities[identity]],
          [Math.sqrt(1 - GENUINE), unitVector()],
        ]),
        embeddingKind: "identity" as const,
      });
    }
  }

  // Every threshold, because the constraint is not a threshold's job. A
  // clusterer may legitimately over-merge people who never posed together on
  // embeddings this poor; it may never put one photo's faces in one tile.
  for (const threshold of [
    DEFAULT_IDENTITY_THRESHOLD,
    SAME_PHOTO_EXCEPTION_SIMILARITY,
    0.85,
    0.95,
  ]) {
    const people = clusterFaces(faces, {
      threshold,
      identityMergeThreshold: threshold,
    });
    for (const person of people) {
      assert(
        person.faceCount <= new Set(person.assetIds).size,
        `at threshold ${threshold} a tile holds ${person.faceCount} faces from only ` +
          `${new Set(person.assetIds).size} photos — same-photo faces were fused`,
      );
    }
    const assigned = people.reduce((sum, person) => sum + person.faceCount, 0);
    assert(
      assigned === faces.length,
      `every face must land somewhere at threshold ${threshold} (got ${assigned}/${faces.length})`,
    );
  }
}

// eslint-disable-next-line no-console
console.log("face-cluster constraints self-check passed");
