import type { PickedPhoto } from "../import/picked-photo";
import type { FaceSignal, QualitySignals } from "./quality-signals.ts";

// @ts-expect-error Node's native TypeScript runner requires the extension.
import { selectBestShots } from "./select-best-shots.ts";

type TestPhoto = PickedPhoto & {
  embedding: number[];
  perceptualEmbedding: number[];
  analysis: QualitySignals;
};

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`Selection-quality regression failed: ${message}`);
  }
}

const startedAt = Date.UTC(2026, 7, 26, 10, 0, 0);
const baseGrid = Array.from({ length: 64 }, (_, index) =>
  Math.sin(index * 1.7) + Math.cos(index * 0.43),
);
const burstGrid = baseGrid.map((value, index) => value + Math.sin(index) * 0.03);
const shiftedGrid = baseGrid.map((_, index) => baseGrid[(index + 9) % 64]);
const portraitGrid = rotateGridClockwise(shiftedGrid);
const unrelatedGrid = Array.from({ length: 64 }, (_, index) =>
  Math.sin(index * 0.29 + 1.3) - Math.cos(index * 1.11),
);
const sharedColors = [0.31, -0.14, -0.17, 0.22, -0.08, -0.14, 0.25, -0.11, -0.14, 0.19, -0.07, -0.12];

const burstA = fingerprint(baseGrid, sharedColors);
const burstB = fingerprint(burstGrid, sharedColors.map((value, index) => value + (index % 2 ? 0.001 : -0.001)));
const reframe = fingerprint(shiftedGrid, sharedColors);
const rotatedReframe = fingerprint(portraitGrid, sharedColors);
const unrelated = fingerprint(unrelatedGrid, sharedColors);

const measured = {
  burst: cosine(burstA, burstB),
  reframe: cosine(burstA, reframe),
  portraitLandscape: cosine(burstA, rotatedReframe),
  portraitLandscapeColor: cosine(burstA.slice(64), rotatedReframe.slice(64)),
};

console.log(`CX-16 duplicate fixture measurements ${JSON.stringify(measured)}`);

assert(measured.burst > 0.92, "ordinary burst fixture must exercise the existing hard-cosine path");
assert(measured.reframe < 0.92, "reframe fixture must reproduce the current duplicate escape");
assert(
  measured.portraitLandscape < 0.92 && measured.portraitLandscapeColor > 0.99,
  "orientation fixture must preserve scene colors while changing spatial layout",
);

const duplicateOutcomes = ([
  ["burst", burstB, 4_000, 3_000, 700],
  ["reframe", reframe, 4_000, 3_000, 2_400],
  ["portrait-landscape-upright", reframe, 3_000, 4_000, 3_600],
  ["portrait-landscape", rotatedReframe, 3_000, 4_000, 4_800],
] as const).map(([label, secondEmbedding, secondWidth, secondHeight, delayMs]) => {
  const result = selectBestShots(
    [
      photo(`${label}-a`, burstA, startedAt, 4_000, 3_000),
      photo(`${label}-b`, secondEmbedding, startedAt + delayMs, secondWidth, secondHeight),
    ],
    { count: 2 },
  );
  return [label, result.selected.length] as const;
});
console.log(`CX-16 duplicate selection outcomes ${JSON.stringify(duplicateOutcomes)}`);

const duplicateGuardOutcomes = {
  samePaletteDifferentScene: selectBestShots(
    [
      photo("different-a", burstA, startedAt, 4_000, 3_000),
      photo("different-b", unrelated, startedAt + 2_000, 4_000, 3_000),
    ],
    { count: 2 },
  ).selected.length,
  reframeOutsideBurst: selectBestShots(
    [
      photo("old-a", burstA, startedAt, 4_000, 3_000),
      photo("old-b", reframe, startedAt + 60_000, 4_000, 3_000),
    ],
    { count: 2 },
  ).selected.length,
  rotatedDifferentScene: selectBestShots(
    [
      photo("rotated-different-a", burstA, startedAt, 4_000, 3_000),
      photo(
        "rotated-different-b",
        fingerprint(rotateGridClockwise(unrelatedGrid), sharedColors),
        startedAt + 2_000,
        3_000,
        4_000,
      ),
    ],
    { count: 2 },
  ).selected.length,
};
console.log(
  `CX-16 duplicate guard outcomes ${JSON.stringify(duplicateGuardOutcomes)}`,
);

const cutFace = selectBestShots(
  [
    photo("cut-sharp", burstA, startedAt, 4_000, 3_000, portrait(face({ cutAtEdge: true }), {
      sharpness: 1,
    })),
    photo("clean-soft", burstB, startedAt + 500, 4_000, 3_000, portrait(face(), {
      sharpness: 0.05,
    })),
  ],
  { count: 1 },
);
const rareCutFace = selectBestShots(
  [
    photo("rare-cut-face", burstA, startedAt, 4_000, 3_000, portrait(face({ cutAtEdge: true }))),
    photo("later-clean-scene", unrelated, startedAt + 2 * 60 * 60 * 1_000, 4_000, 3_000),
  ],
  { count: 2 },
);
const subjectFocus = selectBestShots(
  [
    photo("sharp-background-blurred-subject", burstA, startedAt, 4_000, 3_000, portrait(face(), {
      sharpness: 1,
      subjectSharpness: 0.08,
      subjectBackgroundRatio: 0.1,
    })),
    photo("sharp-subject", burstB, startedAt + 500, 4_000, 3_000, portrait(face(), {
      sharpness: 0.45,
      subjectSharpness: 0.82,
      subjectBackgroundRatio: 0.64,
    })),
  ],
  { count: 1 },
);
console.log(
  `CX-16 quality selection outcomes ${JSON.stringify({
    cutFaceWinner: cutFace.selected[0]?.media_id,
    rareCutFaceSelected: rareCutFace.selected.some(({ media_id }) => media_id === "rare-cut-face"),
    subjectFocusWinner: subjectFocus.selected[0]?.media_id,
  })}`,
);

for (const [label, selectedCount] of duplicateOutcomes) {
  assert(
    selectedCount === 1,
    `${label} near-copies should collapse to one take (selected ${selectedCount})`,
  );
}
assert(
  Object.values(duplicateGuardOutcomes).every((selectedCount) => selectedCount === 2),
  `time/spatial guards must retain distinct frames (${JSON.stringify(duplicateGuardOutcomes)})`,
);
assert(
  cutFace.selected[0]?.media_id === "clean-soft",
  `a clean same-take face must outrank a cut face even at the measured sharpness extremes (got ${cutFace.selected[0]?.media_id})`,
);
assert(
  !rareCutFace.selected.some(({ media_id }) => media_id === "rare-cut-face"),
  "a rare-moment waiver must not rescue an automatically selected cut face",
);
assert(
  subjectFocus.selected[0]?.media_id === "sharp-subject",
  `subject focus must beat a razor-sharp background (got ${subjectFocus.selected[0]?.media_id})`,
);

function photo(
  id: string,
  embedding: number[],
  creationTime: number,
  width: number,
  height: number,
  analysis = scene(),
): TestPhoto {
  return {
    id,
    uri: `file:///photos/${id}.jpg`,
    filename: `${id}.jpg`,
    width,
    height,
    mimeType: "image/jpeg",
    source: "device-gallery",
    creationTime,
    embedding,
    perceptualEmbedding: embedding,
    analysis,
  };
}

function face(overrides: Partial<FaceSignal> = {}): FaceSignal {
  return { areaRatio: 0.08, eyesOpen: 0.9, smile: 0.6, cutAtEdge: false, ...overrides };
}

function portrait(
  portraitFace: FaceSignal,
  overrides: Partial<QualitySignals> = {},
): QualitySignals {
  return scene({
    faces: [portraitFace],
    faceCount: 1,
    largestFaceAreaRatio: portraitFace.areaRatio,
    anyFaceCutAtEdge: portraitFace.cutAtEdge,
    category: "portrait",
    ...overrides,
  });
}

function scene(overrides: Partial<QualitySignals> = {}): QualitySignals {
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

function fingerprint(luma: readonly number[], colors: readonly number[]): number[] {
  return normalize([...luma, ...colors]);
}

function rotateGridClockwise(values: readonly number[]): number[] {
  return Array.from({ length: 64 }, (_, index) => {
    const y = Math.floor(index / 8);
    const x = index % 8;
    return values[(7 - x) * 8 + y];
  });
}

function normalize(values: readonly number[]): number[] {
  const magnitude = Math.hypot(...values);
  return values.map((value) => value / magnitude);
}

function cosine(left: readonly number[], right: readonly number[]): number {
  return left.reduce((sum, value, index) => sum + value * right[index], 0) /
    (Math.hypot(...left) * Math.hypot(...right));
}

console.log("selection-quality regression self-checks passed");
