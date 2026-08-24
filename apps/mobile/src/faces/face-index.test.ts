// @ts-expect-error Node's TypeScript runner requires the source extension.
import { DEFAULT_FACE_INDEX_THRESHOLD, createFacePeopleQuery, scanFaceAssets } from "./face-index.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

const boxA = { x: 10, y: 10, width: 40, height: 40 };
const boxB = { x: 70, y: 10, width: 40, height: 40 };

{
  const embeddings = new Map([
    ["photo-a:10", [1, 0, 0]],
    ["photo-b:10", [0.98, 0.2, 0]],
    ["photo-c:10", [0, 1, 0]],
    ["photo-c:70", [0.1, 0.995, 0]],
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
        return embedding;
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
  const pair = [
    { assetId: "base", embedding: [1, 0] },
    { assetId: "near", embedding: [0.94, 0.341] },
  ];
  assert(
    createFacePeopleQuery(pair, 0.9).getPeople().length === 1,
    "cosine~0.94 synthetic variants should merge below the default",
  );
  assert(
    createFacePeopleQuery(pair).getPeople().length === 1,
    "the calibrated default should merge cosine~0.94 variants",
  );
  assert(
    createFacePeopleQuery(pair, 0.97).getPeople().length === 2,
    "a stricter synthetic calibration should split the same variants",
  );
  assert(
    createFacePeopleQuery([
      pair[0],
      { assetId: "different", embedding: [0.8, 0.6] },
    ]).getPeople().length === 2,
    "the calibrated default should split cosine~0.8 examples",
  );
  assert(
    DEFAULT_FACE_INDEX_THRESHOLD > 0.9 &&
      DEFAULT_FACE_INDEX_THRESHOLD < 0.97,
    "the default threshold should sit between tested merge/split values",
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
