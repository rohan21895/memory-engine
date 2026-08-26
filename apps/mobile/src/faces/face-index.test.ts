// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { SAME_PHOTO_EXCEPTION_SIMILARITY } from "./face-cluster.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { CENTERED_FACE_INDEX_THRESHOLD, DEFAULT_FACE_INDEX_THRESHOLD, FACE_INDEX_IDENTITY_MERGE_THRESHOLD, PERCEPTUAL_FACE_INDEX_THRESHOLD, applyConstraintToPeople, createFacePeopleQuery, createPersonIdsByAsset, dedupeFaceBoxes, dedupeFaceObservations, dequantizeEmbedding, faceQualityTier, quantizeEmbedding, scanFaceAssets } from "./face-index.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { CALIBRATION_MAX_THRESHOLD, CALIBRATION_MIN_THRESHOLD } from "./face-calibration.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { takeScanTrace, type FaceFrame } from "./face-detector.ts";

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

{
  const people = [
    {
      id: "person-1",
      faceCount: 2,
      assetIds: ["a", "shared"],
      centroid: [1, 0],
      embeddingKind: "identity" as const,
    },
    {
      id: "person-2",
      faceCount: 3,
      assetIds: ["b", "shared"],
      centroid: [0, 1],
      embeddingKind: "identity" as const,
    },
  ];
  Object.assign(people[0], { weightSum: 0.5, firstAt: 200, lastAt: 300 });
  Object.assign(people[1], { weightSum: 2.5, firstAt: 100, lastAt: 400 });
  let reclusters = 0;
  const merged = applyConstraintToPeople(
    people,
    "must",
    "person-2",
    "person-1",
    () => {
      reclusters += 1;
    },
  );
  const mergedState = people[0] as typeof people[number] & {
    firstAt?: number;
    lastAt?: number;
    weightSum?: number;
  };
  assert(merged, "must-link should directly merge two existing people");
  assert(reclusters === 0, "must-link must not trigger a full recluster");
  assert(people.length === 1, "must-link should remove the absorbed person record");
  assert(people[0].id === "person-1", "the older person record should survive");
  assert(people[0].faceCount === 5, "must-link should add face counts");
  assert(
    people[0].assetIds.join(",") === "a,shared,b",
    "must-link should union asset ids without duplicates",
  );
  assert(
    Math.abs(people[0].centroid[0] - 1 / 6) < 1e-12 &&
      Math.abs(people[0].centroid[1] - 5 / 6) < 1e-12,
    "must-link should blend centroids by quality weightSum",
  );
  assert(
    mergedState.weightSum === 3 &&
      mergedState.firstAt === 100 &&
      mergedState.lastAt === 400,
    "must-link should carry merge weight and widen the capture span",
  );

  const splitPeople = [
    {
      id: "person-1",
      faceCount: 1,
      assetIds: ["a"],
      centroid: [1, 0],
      embeddingKind: "identity" as const,
    },
    {
      id: "person-2",
      faceCount: 1,
      assetIds: ["b"],
      centroid: [0, 1],
      embeddingKind: "identity" as const,
    },
  ];
  let splitReclusters = 0;
  const directlySplit = applyConstraintToPeople(
    splitPeople,
    "cannot",
    "person-1",
    "person-2",
    () => {
      splitReclusters += 1;
    },
  );
  // A cannot-link must NOT recluster, and this asserts the absence on purpose.
  //
  // It used to, and that was a full O(faces x people) rebuild on the JS thread
  // -- minutes on a 17,699-face library -- fired from a tap. It bought nothing.
  // The user can only name a pair by tapping two SEPARATE tiles, so "these are
  // not the same person" is already true of the grouping on screen, and the
  // rebuild spent those minutes reproducing its own input.
  //
  // What makes the answer stick is storage, not rebuilding: `recordConstraint`
  // has already persisted it, and `mergeSimilarPeople` reads it through
  // `resolveConstraints` to refuse the pair at the next consolidation. Case (d)
  // of face-cluster-constraints.test.ts asserts exactly that, with a vacuity
  // guard proving the pair really would have merged otherwise.
  assert(
    !directlySplit && splitReclusters === 0,
    `a cannot-link must not trigger a rebuild (it ran ${splitReclusters} times)`,
  );

  // An identity centroid and a perceptual one live in different spaces, so the
  // cheap merge refuses the pair. The answer must still count: without the
  // fallback the caller reports a merge that never happened.
  const mixedPeople = [
    { id: "person-1", faceCount: 4, assetIds: ["a"], centroid: [1, 0], embeddingKind: "identity" as const },
    { id: "person-2", faceCount: 4, assetIds: ["b"], centroid: [0, 1, 0], embeddingKind: "perceptual" as const },
  ];
  let mixedReclusters = 0;
  const mixedMerged = applyConstraintToPeople(mixedPeople, "must", "person-1", "person-2", () => {
    mixedReclusters += 1;
  });
  assert(
    !mixedMerged && mixedReclusters === 1 && mixedPeople.length === 2,
    "a must-link the fast path cannot express must fall back to a full recluster",
  );
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
  const boxes = dedupeFaceBoxes([
    { x: 10, y: 10, width: 40, height: 40 },
    // IoU 0.648: two slightly shifted detections of the same head.
    { x: 16, y: 13, width: 40, height: 40 },
    // Its centre is within the old distance tolerance, but IoU is only 0.231:
    // this is a neighbouring face, not another detection of the first one.
    { x: 35, y: 10, width: 40, height: 40 },
  ]);
  assert(
    boxes.length === 2 && boxes[1].x === 35,
    "box dedupe removes a heavily overlapping repeat without deleting an adjacent face",
  );
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
  // Three faces spread far enough that EVERY pair sits below the same-photo
  // exception — three people in one frame, all three kept. The spread is
  // derived from the bar: at 0.85 these could sit 40 degrees apart, but in
  // w600k_mbf space the exception is lower, so they have to be further apart to
  // still be three distinct people rather than one face found three times.
  const spreadDegrees = (Math.acos(SAME_PHOTO_EXCEPTION_SIMILARITY) * 180) / Math.PI * 1.15;
  const atDegrees = (degrees: number): number[] => [
    Math.cos((degrees * Math.PI) / 180),
    Math.sin((degrees * Math.PI) / 180),
  ];
  const cleaned = dedupeFaceObservations([
    duplicate,
    { ...duplicate, embedding: atDegrees(spreadDegrees) },
    { ...duplicate, embedding: atDegrees(spreadDegrees * 2) },
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
  // The raw bar is deliberately NOT pinned to a number any more.
  //
  // It used to be pinned to 0.20, the LFW operating point (impostor p99 0.169,
  // genuine p05 0.423 over 1,471 crops). Measured through the same TFLite build
  // on two real libraries, that bar admitted 5.3% and 17.5% of different-person
  // pairs, because LFW pairs are strangers and a family library is relatives.
  // The two libraries wanted bars 36% apart, so no constant is correct for both
  // and the bar is now measured per library by `calibrateThreshold`. Asserting
  // an exact value here would re-freeze the assumption that a single number can
  // be right, which is the bug this replaced. Pin the INVARIANTS instead.
  assert(
    DEFAULT_FACE_INDEX_THRESHOLD >= CALIBRATION_MIN_THRESHOLD &&
      DEFAULT_FACE_INDEX_THRESHOLD <= CALIBRATION_MAX_THRESHOLD,
    "the cold-start bar must sit inside the range calibration may return, or " +
      "the first scan and every later one disagree by construction",
  );
  assert(
    DEFAULT_FACE_INDEX_THRESHOLD < FACE_INDEX_IDENTITY_MERGE_THRESHOLD,
    "assignment must stay strictly easier than merging: merge errors are " +
      "unrecoverable, assignment errors are one manual split away",
  );
  // Uncalibrated, the app must fail toward splitting rather than fusing. 0.264
  // was the easier of the two measured libraries' impostor p99; a cold-start
  // bar below that is known to merge strangers on real photos.
  assert(
    DEFAULT_FACE_INDEX_THRESHOLD > 0.264,
    "the cold-start bar must clear the impostor tail measured on real libraries",
  );
  assert(
    CENTERED_FACE_INDEX_THRESHOLD === 0.17,
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

{
  takeScanTrace();
  const asset = { id: "group-photo", width: 4000, height: 3000 };
  const primaryFrame: FaceFrame = {
    image: undefined,
    uri: "primary-frame",
    width: 1280,
    height: 960,
    sourceWidth: asset.width,
    sourceHeight: asset.height,
    scale: 0.32,
    temporary: true,
  };
  const detailBounds: number[] = [];
  const closedFrames: string[] = [];
  const embeddedDetailFrames: Array<FaceFrame | null> = [];
  const observations = await scanFaceAssets([asset], {
    isDetectionAvailable: () => true,
    openFrame: async () => primaryFrame,
    openDetailFrame: async (_uri, _asset, bound) => {
      detailBounds.push(bound);
      return {
        image: undefined,
        uri: "detail-frame",
        width: bound,
        height: Math.round((bound * asset.height) / asset.width),
        sourceWidth: asset.width,
        sourceHeight: asset.height,
        scale: bound / asset.width,
        temporary: true,
      };
    },
    closeFrame: async (frame) => {
      closedFrames.push(frame.uri);
    },
    detectFaces: async () => [
      { x: 100, y: 100, width: 250, height: 250 },
      { x: 1000, y: 100, width: 150, height: 150 },
    ],
    embedFace: async (_asset, _uri, box, frame, photo) => {
      assert(frame === primaryFrame, "every face should retain the primary frame");
      assert(
        photo?.detailFrameBound === 2987,
        "every face should receive the smallest face's detail bound",
      );
      embeddedDetailFrames.push(photo?.detailFrame ?? null);
      return {
        embedding: box.x < 500 ? [1, 0] : [0, 1],
        kind: "identity",
      };
    },
  });
  const trace = takeScanTrace();
  assert(observations.length === 2, "both group-photo faces should be embedded");
  assert(
    detailBounds.join(",") === "2987",
    "one detail frame should use the smallest face's largest required bound",
  );
  assert(
    embeddedDetailFrames.length === 2 &&
      embeddedDetailFrames.every((frame) => frame?.uri === "detail-frame"),
    "every small face should reuse the same detail frame",
  );
  assert(
    closedFrames.join(",") === "detail-frame,primary-frame",
    "the shared detail frame should close when its photo is done",
  );
  assert(
    trace.includes("smallFaceMultiPhotos=1") &&
      trace.includes("smallFaceRedundantDecodes=1"),
    "the scan trace should measure multi-face photos and avoided decodes",
  );
}
