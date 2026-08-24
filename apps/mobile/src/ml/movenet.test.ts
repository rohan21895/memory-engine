// @ts-expect-error Node requires the extension; Metro resolves it too.
import { parseMoveNetOutput, rgbaToRgb } from "./movenet.ts";

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

// rgbaToRgb drops alpha without changing channel order.
equal(
  Array.from(rgbaToRgb(new Uint8Array([10, 20, 30, 40, 50, 60, 70, 80]), 2, 1)),
  [10, 20, 30, 50, 60, 70],
  "rgbaToRgb drops alpha",
);
let threw = false;
try {
  rgbaToRgb(new Uint8Array(3), 1, 1);
} catch {
  threw = true;
}
assert(threw, "rgbaToRgb rejects a buffer too small for width*height*4");

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
