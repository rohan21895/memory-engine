// @ts-expect-error Node's native TypeScript runner requires the extension.
import { bestSmile, classifyCategory, isScreenshotOrDocument, significantFaces, worstEyesOpen } from "./quality-signals.ts";
import type { FaceSignal } from "./quality-signals.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`Quality-signals self-check failed: ${message}`);
  }
}

assert(classifyCategory(0, 0) === "scene", "zero faces should be a scene");
assert(
  classifyCategory(1, 0.08) === "portrait",
  "one large face should be a portrait",
);
assert(
  classifyCategory(1, 0.02) === "detail",
  "one small face should remain detail",
);
assert(
  classifyCategory(2, 0.04) === "couple",
  "two non-tiny faces should be a couple",
);
assert(
  classifyCategory(4, 0.03) === "group",
  "three or more non-tiny faces should be a group",
);
assert(
  classifyCategory(5, 0.005) === "detail",
  "only tiny incidental faces should remain detail",
);

assert(
  isScreenshotOrDocument({
    filename: "Screenshot_2026-08-24.png",
    width: 1_170,
    height: 2_532,
  }),
  "a named phone screenshot with a screen ratio should be flagged",
);
assert(
  !isScreenshotOrDocument({
    filename: "IMG_1042.jpg",
    width: 1_170,
    height: 2_532,
  }),
  "screen-like dimensions alone should not hide a real photo",
);
assert(
  !isScreenshotOrDocument({
    filename: "family-photo.png",
    width: 6_000,
    height: 4_000,
  }),
  "PNG alone should not be enough without a screen-like ratio",
);
assert(
  isScreenshotOrDocument({
    filename: "receipt.jpg",
    width: 400,
    height: 1_800,
  }),
  "an extreme receipt-like aspect ratio should be flagged",
);
assert(
  !isScreenshotOrDocument(
    { filename: "Screenshot.png", width: 1_170, height: 2_532 },
    false,
  ),
  "an explicit detector hint should override the heuristic",
);

const faces: FaceSignal[] = [
  { areaRatio: 0.002, eyesOpen: 0.05, smile: 0.9, cutAtEdge: false },
  { areaRatio: 0.08, eyesOpen: 0.8, smile: 0.2, cutAtEdge: false },
  { areaRatio: 0.04, eyesOpen: undefined, smile: 0.7, cutAtEdge: true },
];
const importantFaces = significantFaces(faces, 0.005);
assert(importantFaces.length === 2, "tiny incidental faces should be ignored");
assert(
  worstEyesOpen(importantFaces) === 0.8,
  "unknown eye state should be neutral in the worst-eye aggregate",
);
assert(
  bestSmile(importantFaces) === 0.7,
  "best smile should use the strongest known significant face",
);
assert(
  worstEyesOpen([{ areaRatio: 0.1, cutAtEdge: false }]) === undefined,
  "all-unknown eye state should remain unknown",
);
