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
  source.includes("const boxesPromise = deepAnalysisTiming") &&
    source.includes("detectFaces(analysisUri, {"),
  "face detection must be shared with quality measurement",
);
// The `onDegraded` half is new and belongs in the SAME assertion: the branch a
// gate does not name is the branch that loses it. A decode that dies on the
// no-face branch degrades exactly as much as one that dies with a box.
assert(
  source.includes("? measureImageQuality(analysisUri, { subjectBox, onDegraded })") &&
    source.includes(": measureImageQuality(analysisUri, { onDegraded });"),
  "quality must receive a subject box only when a detected face provides one, and must report a failed decode on either branch",
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
// The callback is pinned alongside the call because `run` NEVER rejects: on
// failure it returns a URI-seeded fallback embedding, which is a valid-looking
// 32-value vector unrelated to the pixels. Nothing downstream can detect that,
// so the only evidence it happened is this argument being passed.
assert(
  source.includes(
    "model.run(analysisUri, (error) => timing.recordDegraded(error)),",
  ),
  "the real heavy-analysis bridge must produce the perceptual fingerprint, and must report when it fell back",
);
assert(
  !source.includes("? Promise.resolve({ embedding: [], faces: 0 })"),
  "the >500-photo path must not bypass the perceptual fingerprint used by CX-16 dedupe",
);

// --- M2: the Tier-B signal store -------------------------------------------
//
// These are source checks, and source checks are weak, so they are deliberately
// confined to the two things that CANNOT be tested any other way from Node:
// which side of the flag ships, and where in the sequence the store is touched.
// The behaviour itself -- the codec, the eviction, the queue ordering and the
// scatter back into candidate order -- is tested properly in
// deep-signal-store.test.ts, analysis-tiers.test.ts and
// deep-signal-parity.test.ts, against real values rather than substrings.

assert(
  source.includes("const USE_DEEP_SIGNAL_CACHE = false;"),
  "the Tier-B store must ship OFF: reading a stored signal instead of recomputing " +
    "one is a selection-affecting change and the default has to stay today's behaviour",
);
// VACUITY: the grep above passes on any file containing that string, including
// one where the constant is never consulted. The flag has to reach a decision.
assert(
  source.includes("options.deepSignalCache ?? USE_DEEP_SIGNAL_CACHE"),
  "VACUITY: ...and the constant must actually be read to decide whether to open the store",
);

const deepLoad = source.indexOf("await deepStore.load(");
const deepPass = source.indexOf("const orderedAnalyses = await mapLimit(");
assert(
  deepLoad >= 0 && deepPass >= 0 && deepLoad < deepPass,
  "the store must be told which months to open BEFORE the deep pass, not during it",
);
assert(
  source.indexOf("chooseHeavyAnalysisCandidates(") < deepLoad,
  "and AFTER the candidate cap: opening every month would read the whole library",
);

// A degraded photograph must never be cached. The perceptual fallback is seeded
// from the URI and the proxy URI is a fresh temp file every build, so a stored
// degraded record pins a valid-looking embedding unrelated to the pixels.
assert(
  source.includes("if (photoDegraded) {\n        analysisQueue.release(job);\n      } else {\n        deepStore?.set("),
  "the store write must sit on the else branch of the degradation check",
);
assert(
  !/deepStore\?\.set\([^)]*\);\s*\n\s*(?:if \(photoDegraded|\/\/ degraded)/.test(source),
  "VACUITY: there must be no second, unguarded write path",
);
assert(
  (source.match(/deepStore\?\.set\(/g) ?? []).length === 1,
  "exactly one place may write a record, or the degradation guard is not a guard",
);

// Cancellation and backgrounding must commit whatever finished.
assert(
  source.includes("await probeCache?.persist();\n    await deepStore?.persist();"),
  "both cache tiers must be checkpointed together on background and in the finally",
);
assert(
  (source.match(/await deepStore\?\.persist\(\);/g) ?? []).length >= 2,
  "one persist is not enough: the lifecycle watcher and the finally are different paths",
);

assert(
  source.includes("restoreInputOrder(analysisOrder, orderedAnalyses)"),
  "the queue's processing order must be undone before anything downstream reads it",
);
assert(
  source.includes("isPermutationOf(queueOrder, analysisInputs.length)"),
  "the reorder must be refused unless it is a permutation, while a fallback still exists",
);
