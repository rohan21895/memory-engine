// @ts-expect-error Node's TypeScript runner requires the source extension.
import { DEFAULT_FACE_INDEX_THRESHOLD, PERCEPTUAL_FACE_INDEX_THRESHOLD, createFacePeopleQuery, createPersonIdsByAsset, dedupeFaceBoxes, dedupeFaceObservations, dequantizeEmbedding, faceQualityTier, quantizeEmbedding, scanFaceAssets } from "./face-index.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

const boxA = { x: 10, y: 10, width: 64, height: 64 };
const boxB = { x: 110, y: 10, width: 64, height: 64 };

assert(
  faceQualityTier({ id: "seed", width: 1000, height: 1000 }, { ...boxA, headEulerAngleY: 30 }) === "seedable",
  "a 64px frontal face can seed a person",
);

{
  const source = Array.from({ length: 192 }, (_, index) =>
    Math.sin(index * 0.7) * 0.2,
  );
  const stored = quantizeEmbedding(source);
  const restored = dequantizeEmbedding(stored);
  assert(stored.length === 256, "a 192-float embedding stores in 256 base64 chars");
  assert(restored.length === source.length, "quantization preserves dimensions");
  assert(
    restored.every((value, index) => Math.abs(value - source[index]) <= 1 / 127),
    "int8 quantization stays within one step",
  );
}

{
  const reverse = createPersonIdsByAsset([
    { id: "person-2", assetIds: ["shared", "b"] },
    { id: "person-1", assetIds: ["a", "shared"] },
  ]);
  assert(reverse.get("shared")?.join(",") === "person-1,person-2", "reverse person lookup is precomputed and sorted");
}
assert(
  faceQualityTier({ id: "assign", width: 1000, height: 1000 }, { ...boxA, width: 40, height: 40, headEulerAngleY: 45 }) === "assignable",
  "a smaller profile can only join an existing person",
);
assert(
  faceQualityTier({ id: "reject", width: 1000, height: 1000 }, { ...boxA, headEulerAngleY: 45.1 }) === null,
  "yaw beyond 45 degrees is discarded",
);

{
  const embeddings = new Map([
    ["photo-a:10", [1, 0, 0]],
    ["photo-b:10", [0.98, 0.2, 0]],
    ["photo-c:10", [0, 1, 0]],
    ["photo-c:110", [0.1, 0.995, 0]],
    ["photo-d:10", [1, 0.02, 0]],
  ]);
  const observations = await scanFaceAssets(
    [
      { id: "photo-a", width: 200, height: 100 },
      { id: "photo-b", width: 200, height: 100 },
      { id: "photo-c", width: 200, height: 100 },
      { id: "photo-d", width: 200, height: 100 },
    ],
    {
      isDetectionAvailable: () => true,
      detectFaces: async (uri) =>
        uri.endsWith("/photo-c") ? [boxA, boxB] : [boxA],
      embedFace: async (asset, _uri, box) => {
        const embedding = embeddings.get(`${asset.id}:${box.x}`);
        if (!embedding) {
          throw new Error("missing mock embedding");
        }
        return { embedding, kind: "identity" };
      },
    },
  );
  const query = createFacePeopleQuery(observations);
  const people = query.getPeople();

  assert(people.length === 2, "distinct mock faces should form two people");
  assert(people[0].faceCount === 3, "largest person should sort first");
  assert(
    people[0].coverAssetId === "photo-a",
    "cover should be the first representative asset",
  );
  assert(
    people[0].assetIds.join(",") === "photo-a,photo-b,photo-d",
    "near-identical faces should span their source asset IDs",
  );
  assert(
    query.assetIdsForPerson(people[1].id).join(",") === "photo-c",
    "person lookup should return unique asset IDs",
  );
  assert(
    query.assetIdsForPerson("missing").length === 0,
    "unknown people should return an empty asset list",
  );
}

{
  const boxes = dedupeFaceBoxes([
    boxA,
    { x: 12, y: 11, width: 39, height: 39 },
    { x: 19, y: 19, width: 60, height: 60 },
    boxB,
  ]);
  assert(boxes.length === 2, "same-center boxes at different scales are removed but a neighboring face remains");
}

{
  const duplicate = { assetId: "same-photo", embedding: [1, 0], embeddingKind: "identity" as const };
  const cleaned = dedupeFaceObservations([
    duplicate,
    { ...duplicate, embedding: [0.78, 0.626] },
    { ...duplicate, embedding: [0.68, 0.733] },
  ]);
  assert(cleaned.length === 2, "same-photo identity repeats are removed without dropping a genuine co-face");
}

{
  const pair = [
    { assetId: "base", embedding: [1, 0], embeddingKind: "identity" as const },
    { assetId: "near", embedding: [0.94, 0.341], embeddingKind: "identity" as const },
  ];
  assert(
    createFacePeopleQuery(pair, 0.9).getPeople().length === 1,
    "identity variants should merge when above the requested threshold",
  );
  assert(
    createFacePeopleQuery(pair).getPeople().length === 1,
    "the MobileFaceNet default should merge cosine~0.94 variants",
  );
  assert(
    createFacePeopleQuery(pair, 0.97).getPeople().length === 2,
    "a stricter synthetic calibration should split the same variants",
  );
  assert(
    createFacePeopleQuery([
      pair[0],
      { assetId: "different", embedding: [0.4, 0.9165], embeddingKind: "identity" },
    ]).getPeople().length === 2,
    "the MobileFaceNet default should split examples below cosine 0.5",
  );
  assert(
    DEFAULT_FACE_INDEX_THRESHOLD === 0.62,
    "identity default is the post-alignment calibration (0.5 was for unaligned crops)",
  );
  assert(
    PERCEPTUAL_FACE_INDEX_THRESHOLD > 0.9,
    "the fallback must retain its conservative perceptual threshold",
  );
}

{
  let detectorCalled = false;
  const observations = await scanFaceAssets(
    [{ id: "photo-a", width: 100, height: 100 }],
    {
      isDetectionAvailable: () => false,
      detectFaces: async () => {
        detectorCalled = true;
        throw new Error("must not run");
      },
      embedFace: async () => {
        throw new Error("must not run");
      },
    },
  );
  assert(
    observations.length === 0,
    "unavailable detection should yield no faces",
  );
  assert(!detectorCalled, "unavailable detection should not call native code");
  assert(
    createFacePeopleQuery(observations).getPeople().length === 0,
    "unavailable detection should expose zero people",
  );
}

{
  const query = createFacePeopleQuery([
    { assetId: "identity", embedding: [1, 0], embeddingKind: "identity" },
    { assetId: "fallback", embedding: [1, 0], embeddingKind: "perceptual" },
  ]);
  assert(
    query.getPeople().length === 2,
    "query projection must not merge identity and fallback observations",
  );
}

{
  const observations = await scanFaceAssets([], {
    isDetectionAvailable: () => true,
    detectFaces: async () => {
      throw new Error("empty scans must not call detection");
    },
    embedFace: async () => {
      throw new Error("empty scans must not call embedding");
    },
  });
  assert(observations.length === 0, "an empty library should scan without faces");
  assert(
    createFacePeopleQuery(observations).getPeople().length === 0,
    "an empty library should expose no people",
  );
}
