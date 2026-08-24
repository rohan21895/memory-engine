// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { ARCFACE_TEMPLATE_112, alignFaceRgb, alignmentPairs, similarityTransform, warpFaceRgb } from "./face-align.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-align self-check failed: ${message}`);
}

function near(actual: number, expected: number, tolerance: number, message: string): void {
  assert(
    Math.abs(actual - expected) <= tolerance,
    `${message} (got ${actual}, want ${expected} ±${tolerance})`,
  );
}

const template = ARCFACE_TEMPLATE_112.map(([x, y]) => ({ x, y }));

// Landmarks already on the template must produce the identity transform.
const identity = similarityTransform(template, ARCFACE_TEMPLATE_112);
assert(identity, "template landmarks yield a transform");
near(identity.a, 1, 1e-6, "identity scale·cos");
near(identity.b, 0, 1e-6, "identity scale·sin");
near(identity.tx, 0, 1e-4, "identity tx");
near(identity.ty, 0, 1e-4, "identity ty");

// A face rotated by 30 degrees and scaled 2x about the origin must be undone:
// the recovered transform maps those points back onto the template.
const theta = Math.PI / 6;
const scale = 2;
const rotated = template.map(({ x, y }) => ({
  x: scale * (x * Math.cos(theta) - y * Math.sin(theta)) + 17,
  y: scale * (x * Math.sin(theta) + y * Math.cos(theta)) - 9,
}));
const recovered = similarityTransform(rotated, ARCFACE_TEMPLATE_112);
assert(recovered, "rotated+scaled landmarks yield a transform");
// Applying it to the rotated points must land back on the template.
rotated.forEach((point, index) => {
  const u = recovered.a * point.x - recovered.b * point.y + recovered.tx;
  const v = recovered.b * point.x + recovered.a * point.y + recovered.ty;
  near(u, ARCFACE_TEMPLATE_112[index][0], 1e-3, `aligned x[${index}]`);
  near(v, ARCFACE_TEMPLATE_112[index][1], 1e-3, `aligned y[${index}]`);
});
// Recovered scale is the inverse of the applied one.
near(Math.hypot(recovered.a, recovered.b), 1 / scale, 1e-6, "recovered scale");

// Degenerate input (all landmarks identical) must fail closed, not divide by zero.
const collapsed = template.map(() => ({ x: 5, y: 5 }));
assert(similarityTransform(collapsed, ARCFACE_TEMPLATE_112) === undefined, "collapsed landmarks rejected");
assert(
  similarityTransform([{ x: 0, y: 0 }], [[0, 0]]) === undefined,
  "a single point cannot define a similarity transform",
);
assert(
  similarityTransform([{ x: Number.NaN, y: 0 }, { x: 1, y: 1 }], [[0, 0], [1, 1]]) === undefined,
  "non-finite landmarks rejected",
);

// warpFaceRgb samples the source: a 2x2 image warped by the identity transform
// keeps its corner colors in the matching corners.
const src = new Uint8Array([
  255, 0, 0, 255, /**/ 0, 255, 0, 255,
  0, 0, 255, 255, /**/ 255, 255, 0, 255,
]);
const warped = warpFaceRgb(src, 2, 2, { a: 1, b: 0, tx: 0, ty: 0 }, 2);
assert(warped, "identity warp returns pixels");
assert(warped.length === 2 * 2 * 3, "warp drops the alpha channel");
near(warped[0], 255, 1, "top-left stays red");
near(warped[1], 0, 1, "top-left has no green");
near(warped[3], 0, 1, "top-right has no red");
near(warped[4], 255, 1, "top-right stays green");
near(warped[8], 255, 1, "bottom-left stays blue");

// A too-small buffer must fail closed rather than read past the end.
assert(warpFaceRgb(new Uint8Array(4), 2, 2, { a: 1, b: 0, tx: 0, ty: 0 }, 2) === undefined, "short buffer rejected");

// ── The crossed left/right mapping (the bug that silently mirrors faces) ──
// ML Kit names landmarks from the SUBJECT's view; the template is in IMAGE
// space. So the subject's RIGHT eye must pair with template[0] (image-left).
// An upright face: subject's right eye appears on the image LEFT (smaller x).
const upright = {
  rightEye: { x: 40, y: 50 }, // subject's right → image left
  leftEye: { x: 72, y: 50 }, // subject's left  → image right
  noseBase: { x: 56, y: 70 },
  rightMouth: { x: 43, y: 92 },
  leftMouth: { x: 69, y: 92 },
};
const pairs = alignmentPairs(upright);
assert(pairs, "upright face yields alignment pairs");
assert(pairs.src.length === 4, "mouth corners present ⇒ 4-point fit (nose dropped)");
assert(pairs.src[0] === upright.rightEye, "template[0] (image-left) takes the SUBJECT'S RIGHT eye");
assert(pairs.src[1] === upright.leftEye, "template[1] (image-right) takes the SUBJECT'S LEFT eye");
near(pairs.dst[0][0], ARCFACE_TEMPLATE_112[0][0], 1e-9, "dst[0] is the image-left template eye");

// The recovered transform must NOT mirror: a similarity transform preserves
// orientation, so the image-left eye stays left of the image-right eye.
const uprightTransform = similarityTransform(pairs.src, pairs.dst);
assert(uprightTransform, "upright face yields a transform");
const mapX = (p: { x: number; y: number }) =>
  uprightTransform.a * p.x - uprightTransform.b * p.y + uprightTransform.tx;
assert(
  mapX(upright.rightEye) < mapX(upright.leftEye),
  "subject's right eye lands LEFT of subject's left eye (no mirroring)",
);
near(mapX(upright.rightEye), ARCFACE_TEMPLATE_112[0][0], 2.0, "right eye lands on template[0].x");
near(mapX(upright.leftEye), ARCFACE_TEMPLATE_112[1][0], 2.0, "left eye lands on template[1].x");

// Falls back to eyes+nose when the mouth corners are unusable.
const noMouth = alignmentPairs({
  rightEye: { x: 40, y: 50 },
  leftEye: { x: 72, y: 50 },
  noseBase: { x: 56, y: 70 },
  rightMouth: { x: Number.NaN, y: 0 },
  leftMouth: { x: 69, y: 92 },
});
assert(noMouth && noMouth.src.length === 3, "missing mouth corner ⇒ eyes+nose fit");

// Falls back to the eyes alone when nothing else survives.
const eyesOnly = alignmentPairs({
  rightEye: { x: 40, y: 50 },
  leftEye: { x: 72, y: 50 },
  rightMouth: { x: Number.NaN, y: 0 },
  leftMouth: { x: Number.NaN, y: 0 },
});
assert(eyesOnly && eyesOnly.src.length === 2, "eyes alone still pin a similarity transform");

// No usable eyes ⇒ undefined, so the caller falls back to the bbox crop.
assert(
  alignmentPairs({
    rightEye: { x: Number.NaN, y: 0 },
    leftEye: { x: 72, y: 50 },
    rightMouth: { x: 43, y: 92 },
    leftMouth: { x: 69, y: 92 },
  }) === undefined,
  "unusable eyes reject alignment",
);

// End-to-end: returns a 112x112 RGB face.
const big = new Uint8Array(120 * 120 * 4).fill(128);
const aligned = alignFaceRgb(big, 120, 120, upright);
assert(aligned && aligned.length === 112 * 112 * 3, "aligned face is 112x112 RGB");

// Missing landmarks degrade to undefined so callers can fall back to a bbox crop.
const missing = alignFaceRgb(big, 120, 120, {
  rightEye: { x: Number.NaN, y: 0 },
  leftEye: { x: 73.5, y: 51.5 },
  rightMouth: { x: 41.5, y: 92.4 },
  leftMouth: { x: 70.7, y: 92.2 },
});
assert(missing === undefined, "unusable landmarks fall back instead of throwing");

// eslint-disable-next-line no-console
console.log("face-align self-check passed");
