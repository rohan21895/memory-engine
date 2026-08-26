// @ts-expect-error Node requires the extension; Metro resolves it too.
import { letterboxLayout, letterboxRgbaToRgb, parseMoveNetOutput } from "./movenet.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`MoveNet self-check failed: ${message}`);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(
    JSON.stringify(actual) === JSON.stringify(expected),
    `${message} (got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)})`,
  );
}

// The letterbox scales UNIFORMLY: the same factor on both axes, whichever way
// round the frame is. That is the property that matters, not the exact numbers
// -- pose.ts reads MoveNet's output as joint angles, and angles survive a
// uniform scale but not the non-uniform squash this replaced.
for (const [width, height] of [[4000, 3000], [3000, 4000], [1000, 1000], [8000, 1000]]) {
  const layout = letterboxLayout(width, height, 192);
  assert(
    Math.abs(layout.drawWidth / width - layout.drawHeight / height) < 1e-3,
    `letterbox ${width}x${height} scales both axes by one factor`,
  );
  assert(
    layout.drawWidth <= 192 && layout.drawHeight <= 192,
    `letterbox ${width}x${height} fits inside the model input`,
  );
  assert(
    layout.drawWidth === 192 || layout.drawHeight === 192,
    `letterbox ${width}x${height} fills the long edge`,
  );
}
equal(
  letterboxLayout(4000, 3000, 192),
  { drawWidth: 192, drawHeight: 144 },
  "a 4:3 landscape frame letterboxes to 192x144",
);
equal(
  letterboxLayout(3000, 4000, 192),
  { drawWidth: 144, drawHeight: 192 },
  "a 3:4 portrait frame letterboxes to 144x192",
);

// letterboxRgbaToRgb drops alpha, keeps channel order, and centres the decoded
// frame in a zero-padded square using the DECODED size.
equal(
  Array.from(
    letterboxRgbaToRgb(new Uint8Array([10, 20, 30, 40, 50, 60, 70, 80]), 2, 1, 2),
  ),
  // 2x1 centred in a 2x2: offsetY = floor((2 - 1) / 2) = 0, so row 0 holds the
  // pixels and row 1 stays padding.
  [10, 20, 30, 50, 60, 70, 0, 0, 0, 0, 0, 0],
  "letterboxRgbaToRgb drops alpha and pads with zeros",
);

const padded = letterboxRgbaToRgb(
  new Uint8Array([1, 2, 3, 255, 4, 5, 6, 255]),
  1,
  2,
  4,
);
equal(padded.length, 4 * 4 * 3, "output is always size*size*3");
// 1x2 centred in a 4x4: offsetX = 1, offsetY = 1.
equal(
  Array.from(padded.slice((1 * 4 + 1) * 3, (1 * 4 + 1) * 3 + 3)),
  [1, 2, 3],
  "the first source pixel lands at the centred offset",
);
equal(
  Array.from(padded.slice((2 * 4 + 1) * 3, (2 * 4 + 1) * 3 + 3)),
  [4, 5, 6],
  "the second source row lands one model row lower",
);
equal(padded[0], 0, "the padding border stays black");

let threw = false;
try {
  letterboxRgbaToRgb(new Uint8Array(3), 1, 1);
} catch {
  threw = true;
}
assert(threw, "letterboxRgbaToRgb rejects a buffer too small for width*height*4");

threw = false;
try {
  letterboxLayout(0, 100);
} catch {
  threw = true;
}
assert(threw, "letterboxLayout rejects a non-positive dimension");

// parseMoveNetOutput maps y-x-score tensors to x-y keypoints.
const values = new Float32Array(17 * 3);
for (let index = 0; index < 17; index += 1) {
  values[index * 3] = 0.1 + index / 100;
  values[index * 3 + 1] = 0.2 + index / 100;
  values[index * 3 + 2] = 0.9;
}
const parsed = parseMoveNetOutput(values.buffer);
assert(parsed, "parseMoveNetOutput returns a result for a full 17x3 tensor");
equal(parsed.keypoints[0], [values[1], values[0]], "keypoint 0 is (x=values[1], y=values[0])");
equal(parsed.keypoints.length, 17, "17 keypoints parsed");
equal(parsed.scores[16], values[50], "score[16] = values[50]");
equal(parseMoveNetOutput(new ArrayBuffer(4)), undefined, "a short buffer yields undefined");

// eslint-disable-next-line no-console
console.log("movenet self-check passed");
