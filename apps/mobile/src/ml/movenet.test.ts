import assert from "node:assert/strict";
import test from "node:test";

// @ts-expect-error Node requires the extension; Metro resolves it too.
import { parseMoveNetOutput, rgbaToRgb } from "./movenet.ts";

test("rgbaToRgb drops alpha without changing channel order", () => {
  assert.deepEqual(
    Array.from(
      rgbaToRgb(
        new Uint8Array([10, 20, 30, 40, 50, 60, 70, 80]),
        2,
        1,
      ),
    ),
    [10, 20, 30, 50, 60, 70],
  );
  assert.throws(() => rgbaToRgb(new Uint8Array(3), 1, 1));
});

test("parseMoveNetOutput maps y-x-score tensors to x-y keypoints", () => {
  const values = new Float32Array(17 * 3);
  for (let index = 0; index < 17; index += 1) {
    values[index * 3] = 0.1 + index / 100;
    values[index * 3 + 1] = 0.2 + index / 100;
    values[index * 3 + 2] = 0.9;
  }
  const parsed = parseMoveNetOutput(values.buffer);
  assert(parsed);
  assert.deepEqual(parsed.keypoints[0], [values[1], values[0]]);
  assert.equal(parsed.keypoints.length, 17);
  assert.equal(parsed.scores[16], values[50]);
  assert.equal(parseMoveNetOutput(new ArrayBuffer(4)), undefined);
});
