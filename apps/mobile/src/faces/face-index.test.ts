// @ts-expect-error Node's TypeScript runner requires the source extension.
import { CENTERED_FACE_INDEX_THRESHOLD, DEFAULT_FACE_INDEX_THRESHOLD, PERCEPTUAL_FACE_INDEX_THRESHOLD, createFacePeopleQuery, createPersonIdsByAsset, dedupeFaceBoxes, dedupeFaceObservations, dequantizeEmbedding, faceQualityTier, quantizeEmbedding, scanFaceAssets } from "./face-index.ts";

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
  // CHANGED (face-index v20): the dedupe bar moved 0.75 -> 0.85 to match
  // SAME_PHOTO_EXCEPTION_SIMILARITY. Both rules answer "are these two boxes in
  // one photo the same person?" and they used to disagree across the whole
  // 0.75-0.85 band: clustering treated a same-photo pair at cosine 0.80 as two
  // people who merely posed together, while this function had already deleted
  // one of them as a duplicate detection. Siblings and parent/child pairs land
  // in that band, so the second person was destroyed before clustering saw
  // them. Hence the 0.78 co-face below is now KEPT (three, not two).
  const duplicate = { assetId: "same-photo", embedding: [1, 0], embeddingKind: "identity" as const };
  // Three faces at 0, 40 and 80 degrees: every pair sits at cosine 0.77 or
  // lower, i.e. inside the old 0.75-0.85 disagreement band but below the
  // same-photo exception. Three people in one frame, all three kept.
  const cleaned = dedupeFaceObservations([
    duplicate,
    { ...duplicate, embedding: [0.766, 0.643] },
    { ...duplicate, embedding: [0.1736, 0.9848] },
  ]);
  assert(cleaned.length === 3, "co-faces below the same-photo exception survive as separate people");
  const repeats = dedupeFaceObservations([
    duplicate,
    { ...duplicate, embedding: [0.995, 0.0999] },
    { ...duplicate, embedding: [0.68, 0.733] },
  ]);
  assert(repeats.length === 2, "a genuine repeat detection of one face is still removed");
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
  // Tests the BAR, not a fixed number, so moving the operating point does not
  // silently invalidate the case. pair[0] is a unit vector, so a partner at a
  // chosen angle from it has exactly that cosine.
  {
    const base = pair[0].embedding;
    const partnerAt = (cosine: number) => {
      // Component along `base`, plus an orthogonal component to make it unit.
      const orthogonal = [-base[1], base[0]];
      const sine = Math.sqrt(Math.max(0, 1 - cosine * cosine));
      return [
        base[0] * cosine + orthogonal[0] * sine,
        base[1] * cosine + orthogonal[1] * sine,
      ];
    };
    const split = DEFAULT_FACE_INDEX_THRESHOLD - 0.15;
    const merge = DEFAULT_FACE_INDEX_THRESHOLD + 0.15;
    assert(
      createFacePeopleQuery([
        pair[0],
        { assetId: "different", embedding: partnerAt(split), embeddingKind: "identity" },
      ]).getPeople().length === 2,
      `a pair at cosine ${split.toFixed(2)} sits below the bar and must split`,
    );
    assert(
      createFacePeopleQuery([
        pair[0],
        { assetId: "different", embedding: partnerAt(merge), embeddingKind: "identity" },
      ]).getPeople().length === 1,
      `a pair at cosine ${merge.toFixed(2)} sits above the bar and must merge`,
    );
  }
  // The bar only means something alongside the space it is measured in.
  // 0.62 was calibrated on RAW embeddings, whose population mean has norm 0.845
  // and therefore adds ~0.71 to every cosine — the measured raw impostor median
  // was 0.725, i.e. ABOVE this bar, so it admitted most strangers by
  // construction. Clustering now runs on CENTERED embeddings, where the
  // measured impostor median is -0.015 and p99 is 0.533, so the bar belongs far
  // lower. Move these two together or not at all.
  // 0.40 is an operating point, not a guess: measured on labelled faces with
  // this exact model and correct alignment, it gives TAR 88.0% / FAR 0.63%.
  assert(
    DEFAULT_FACE_INDEX_THRESHOLD === 0.4,
    "the RAW bar is the measured TAR/FAR operating point",
  );
  assert(
    CENTERED_FACE_INDEX_THRESHOLD === 0.35,
    "the CENTERED bar is calibrated for a distribution whose impostor median is ~0",
  );
  // The whole point of keeping two constants: a bar is meaningless without the
  // space it was measured in. Centered must be the LOWER of the two, because
  // removing the shared direction removes ~0.71 from every cosine.
  assert(
    CENTERED_FACE_INDEX_THRESHOLD < DEFAULT_FACE_INDEX_THRESHOLD,
    "centering removes a large constant, so its bar must sit below the raw one",
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
