// Integration-source self-checks for ../build-album.ts. Importing that module
// in Node would eagerly load Expo native modules, so these checks inspect the
// glue and exercise the pure planner contract without booting React Native.
// @ts-expect-error The Expo app deliberately does not ship Node type declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node requires the extension while Metro resolves it too.
import { planAlbum, type PlannerCandidate } from "./album-planner.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Build-album self-check failed: ${message}`);
}

const source = readFileSync(new URL("../build-album.ts", import.meta.url), "utf8");

const initialYield = source.indexOf("await yieldToEventLoop();");
const cacheLoad = source.indexOf("loadCandidateProbeCache()");
assert(
  initialYield >= 0 && cacheLoad >= 0 && initialYield < cacheLoad,
  "the building screen must receive a paint turn before cache/native work starts",
);

assert(
  source.includes("const boxesPromise = detectFaces(analysisUri"),
  "face detection must be shared with quality measurement",
);
assert(
  source.includes("? measureImageQuality(analysisUri, { subjectBox })") &&
    source.includes(": measureImageQuality(analysisUri);"),
  "quality must receive a subject box only when a detected face provides one",
);
assert(
  source.includes("...quality,") &&
    source.includes("dominantFaceSubjectBox(") &&
    source.includes("detectedBoxes,"),
  "subject-region signals must survive the build-album bridge",
);

const faceGatePolicy = {
  qualityFloor: 0,
  faceSharpnessFloor: 1,
  headSharpnessFloor: 1,
  faceExposureFloor: 1,
  maxClippedFraction: 0,
};
const scenery: PlannerCandidate = {
  mediaId: "night-sky-portrait-free",
  quality: 0.9,
  category: "scene",
};
const noFaceRegion = planAlbum([scenery], 1, { policy: faceGatePolicy });
assert(
  noFaceRegion.selectedIds[0] === scenery.mediaId,
  "all face-region gates must stay inert when their signals are absent",
);

for (const failingSignal of [
  { faceSharpness: 0 },
  { headSharpness: 0 },
  { faceExposure: 0 },
  { clippedFraction: 1 },
]) {
  const gated = planAlbum(
    [
      { ...scenery, mediaId: "bad-face-region", ...failingSignal },
      { ...scenery, mediaId: "clean-face-region" },
    ],
    1,
    { policy: faceGatePolicy },
  );
  assert(
    gated.selectedIds[0] === "clean-face-region",
    `face gate must engage for ${Object.keys(failingSignal)[0]}`,
  );
}

assert(
  source.includes(
    "embedding: embeddingForNearDuplicateGrouping(\n        result.embedding,\n        semantic?.embedding,",
  ),
  "near-duplicate grouping must prefer the perceptual embedding",
);
assert(
  source.includes("return hasPerceptualEmbedding") &&
    source.includes(": semanticEmbedding ?? [];"),
  "semantic embedding must remain the fallback when perceptual data is absent",
);
assert(
  source.includes("model.run(analysisUri),"),
  "the real heavy-analysis bridge must produce the perceptual fingerprint",
);
assert(
  !source.includes("? Promise.resolve({ embedding: [], faces: 0 })"),
  "the >500-photo path must not bypass the perceptual fingerprint used by CX-16 dedupe",
);
