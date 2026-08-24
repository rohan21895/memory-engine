import type { PickedPhoto } from "../import/picked-photo";

import type { FaceSignal, QualitySignals } from "./quality-signals.ts";
// @ts-expect-error Node's native TypeScript runner requires the extension.
import { selectBestShots } from "./select-best-shots.ts";

type TestPhoto = PickedPhoto & {
  embedding?: number[];
  analysis?: QualitySignals;
};

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`Selection self-check failed: ${message}`);
  }
}

function includesReason(reasons: string[], text: string): boolean {
  return reasons.some((reason) => reason.toLowerCase().includes(text));
}

const nearDuplicateA = basisEmbedding(0);
const nearDuplicateB = [...nearDuplicateA];
nearDuplicateB[0] = 0.99;
nearDuplicateB[1] = 0.1;

const duplicateResult = selectBestShots(
  [
    photo("burst-a", nearDuplicateA),
    photo("burst-b", nearDuplicateB),
  ],
  { count: 10 },
);

assert(
  duplicateResult.selected.length === 1,
  "high-similarity frames should collapse to one selected take",
);
assert(
  duplicateResult.selected[0].alternatives.some(
    ({ media_id }) => media_id === "burst-b",
  ),
  "the non-selected near-duplicate should be offered as an alternative",
);
assert(
  duplicateResult.pool.some(({ media_id }) => media_id === "burst-b"),
  "an offered alternative should also remain in the pool",
);

// Golden assertions for the unchanged no-analysis path.
assert(
  JSON.stringify(duplicateResult.selected[0].chosen_because) ===
    JSON.stringify([
      "Strongest thumbnail-detail proxy among 2 near-duplicate frames.",
      "Thumbnail contrast/detail proxy: 99%.",
      "12.0 MP source resolution.",
    ]),
  "legacy chosen explanations should remain unchanged without analysis",
);
assert(
  JSON.stringify(duplicateResult.selected[0].alternatives[0].not_chosen_because) ===
    JSON.stringify([
      "Near-duplicate of the chosen frame (cosine similarity 0.995).",
    ]),
  "legacy alternative explanations should remain unchanged without analysis",
);
assert(
  JSON.stringify(duplicateResult.pool[0].reasons) ===
    JSON.stringify([
      "Near-duplicate of the chosen frame (cosine similarity 0.995).",
    ]),
  "legacy pool explanations should remain unchanged without analysis",
);

const distinctPhotos = [
  photo("distinct-a", basisEmbedding(0)),
  photo("distinct-b", basisEmbedding(1)),
  photo("distinct-c", basisEmbedding(2)),
];
const distinctResult = selectBestShots(distinctPhotos, { count: 3 });

assert(
  distinctResult.selected.length === distinctPhotos.length,
  "fully distinct embeddings should all be kept when count allows",
);
assert(
  distinctResult.pool.length === 0,
  "fully selected distinct frames should leave an empty pool",
);

const cappedResult = selectBestShots(distinctPhotos, { count: 2 });
assert(cappedResult.selected.length === 2, "selection must respect count");
assert(
  cappedResult.selected.every(({ page }, index) => page === index + 1),
  "selected pages should be one-based and sequential",
);
assert(
  cappedResult.pool.length === 1,
  "frames beyond count should remain available in the pool",
);
assert(
  JSON.stringify(cappedResult.pool[0].reasons) ===
    JSON.stringify([
      "The album target was already filled with stronger frames from distinct takes.",
    ]),
  "legacy distinct-take pool reason should remain unchanged",
);

const emptyResult = selectBestShots([], { count: 5 });
assert(emptyResult.selected.length === 0, "empty input should select nothing");
assert(emptyResult.pool.length === 0, "empty input should have an empty pool");

const screenshotResult = selectBestShots(
  [
    photo(
      "screenshot",
      basisEmbedding(0),
      signals({ sharpness: 1, isScreenshotOrDocument: true }),
    ),
    photo(
      "real-photo",
      basisEmbedding(1),
      signals({ sharpness: 0.4 }),
    ),
  ],
  { count: 2 },
);
assert(
  screenshotResult.selected.length === 1 &&
    screenshotResult.selected[0].media_id === "real-photo",
  "screenshots should never consume an album slot",
);
const screenshotPool = screenshotResult.pool.find(
  ({ media_id }) => media_id === "screenshot",
);
assert(
  screenshotPool !== undefined &&
    includesReason(screenshotPool.reasons, "screenshot excluded"),
  "screenshots should remain in the pool with an explicit exclusion reason",
);

const blinkingFace = face({ eyesOpen: 0.1, smile: 1 });
const openFace = face({ eyesOpen: 0.85, smile: 0 });
const blinkResult = selectBestShots(
  [
    photo(
      "blink-sharp",
      nearDuplicateA,
      portraitSignals(blinkingFace, { sharpness: 1 }),
    ),
    photo(
      "eyes-open",
      nearDuplicateA,
      portraitSignals(openFace, { sharpness: 0.25 }),
    ),
  ],
  { count: 1 },
);
assert(
  blinkResult.selected[0].media_id === "eyes-open",
  "an all-eyes-open frame should hard-gate a sharper blinking duplicate",
);
assert(
  includesReason(blinkResult.selected[0].chosen_because, "open eyes"),
  "the chosen explanation should cite open eyes",
);
assert(
  includesReason(
    blinkResult.selected[0].alternatives[0].not_chosen_because,
    "subject blinking",
  ),
  "the rejected duplicate should cite blinking",
);

const noFullyOpenResult = selectBestShots(
  [
    photo(
      "more-closed",
      nearDuplicateA,
      portraitSignals(face({ eyesOpen: 0.1 }), { sharpness: 0.7 }),
    ),
    photo(
      "less-closed",
      nearDuplicateA,
      portraitSignals(face({ eyesOpen: 0.3 }), { sharpness: 0.7 }),
    ),
  ],
  { count: 1 },
);
assert(
  noFullyOpenResult.selected[0].media_id === "less-closed",
  "when no frame clears the open-eye threshold, eye state should penalize proportionally",
);

const cutFaceResult = selectBestShots(
  [
    photo(
      "face-cut",
      nearDuplicateA,
      portraitSignals(face({ cutAtEdge: true }), {
        sharpness: 1,
        anyFaceCutAtEdge: true,
      }),
    ),
    photo(
      "clean-edge",
      nearDuplicateA,
      portraitSignals(face(), { sharpness: 0.9 }),
    ),
  ],
  { count: 1 },
);
assert(
  cutFaceResult.selected[0].media_id === "clean-edge",
  "the portrait cut-face penalty should outweigh a small sharpness advantage",
);
assert(
  includesReason(
    cutFaceResult.selected[0].alternatives[0].not_chosen_because,
    "face cut at frame edge",
  ),
  "cut-face rejection should be explained",
);

const sharpnessResult = selectBestShots(
  [
    photo(
      "blurred",
      nearDuplicateA,
      signals({ sharpness: 0.15, category: "scene" }),
    ),
    photo(
      "sharp",
      nearDuplicateA,
      signals({ sharpness: 0.9, category: "scene" }),
    ),
  ],
  { count: 1 },
);
assert(
  sharpnessResult.selected[0].media_id === "sharp",
  "real sharpness should dominate duplicate selection for scenes",
);
assert(
  includesReason(sharpnessResult.selected[0].chosen_because, "sharpest of 2"),
  "the winner should honestly cite being the sharpest duplicate",
);
assert(
  includesReason(
    sharpnessResult.selected[0].alternatives[0].not_chosen_because,
    "blurrier",
  ),
  "the blurred alternative should explain its sharpness loss",
);

const exposureResult = selectBestShots(
  [
    photo(
      "blown-out",
      nearDuplicateA,
      signals({ sharpness: 0.8, exposure: 1, clippedFraction: 1 }),
    ),
    photo(
      "balanced",
      nearDuplicateA,
      signals({ sharpness: 0.8, exposure: 0.5, clippedFraction: 0 }),
    ),
  ],
  { count: 1 },
);
assert(
  exposureResult.selected[0].media_id === "balanced",
  "balanced exposure and low clipping should improve quality",
);

const portraitExpressionResult = selectBestShots(
  [
    photo(
      "portrait-expression",
      nearDuplicateA,
      portraitSignals(face({ eyesOpen: 1, smile: 1 }), { sharpness: 0.7 }),
    ),
    photo(
      "portrait-pixels",
      nearDuplicateA,
      portraitSignals(face({ eyesOpen: 0.5, smile: 0 }), { sharpness: 0.9 }),
    ),
  ],
  { count: 1 },
);
assert(
  portraitExpressionResult.selected[0].media_id === "portrait-expression",
  "portrait weighting should prefer strong eyes and smile over a modest sharpness gain",
);
assert(
  includesReason(
    portraitExpressionResult.selected[0].alternatives[0].not_chosen_because,
    "lower smile signal",
  ),
  "portrait alternatives should explain a meaningful smile difference",
);

const scenePixelsResult = selectBestShots(
  [
    photo(
      "scene-soft",
      nearDuplicateA,
      signals({ sharpness: 0.7, category: "scene" }),
    ),
    photo(
      "scene-sharp",
      nearDuplicateA,
      signals({ sharpness: 0.9, category: "scene" }),
    ),
  ],
  { count: 1 },
);
assert(
  scenePixelsResult.selected[0].media_id === "scene-sharp",
  "scene weighting should prefer overall pixel sharpness",
);

function basisEmbedding(position: number): number[] {
  return Array.from({ length: 64 }, (_, index) =>
    index === position ? 1 : 0,
  );
}

function photo(
  id: string,
  embedding?: number[],
  analysis?: QualitySignals,
): TestPhoto {
  return {
    id,
    uri: `file:///photos/${id}.jpg`,
    filename: `${id}.jpg`,
    width: 4_000,
    height: 3_000,
    mimeType: "image/jpeg",
    source: "device-gallery",
    embedding,
    analysis,
  };
}

function face(overrides: Partial<FaceSignal> = {}): FaceSignal {
  return {
    areaRatio: 0.08,
    eyesOpen: 0.8,
    smile: 0.5,
    cutAtEdge: false,
    ...overrides,
  };
}

function signals(overrides: Partial<QualitySignals> = {}): QualitySignals {
  return {
    sharpness: 0.7,
    exposure: 0.5,
    clippedFraction: 0,
    faces: [],
    faceCount: 0,
    largestFaceAreaRatio: 0,
    anyFaceCutAtEdge: false,
    isScreenshotOrDocument: false,
    category: "scene",
    ...overrides,
  };
}

function portraitSignals(
  portraitFace: FaceSignal,
  overrides: Partial<QualitySignals> = {},
): QualitySignals {
  return signals({
    faces: [portraitFace],
    faceCount: 1,
    largestFaceAreaRatio: portraitFace.areaRatio,
    anyFaceCutAtEdge: portraitFace.cutAtEdge,
    category: "portrait",
    ...overrides,
  });
}
