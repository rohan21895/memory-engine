// @ts-expect-error Node's TypeScript runner requires the source extension.
import { mapDetectedFaces } from "./face-detector.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-detector self-check failed: ${message}`);
}

const numeric = mapDetectedFaces(
  [{
    frame: { origin: { x: 10, y: 20 }, size: { x: 100, y: 80 } },
    landmarks: [
      { type: "4", position: { x: 70, y: 40 } },
      { type: "10", position: { x: 30, y: 40 } },
      { type: "6", position: { x: 50, y: 55 } },
      { type: "5", position: { x: 65, y: 70 } },
      { type: "11", position: { x: 35, y: 70 } },
    ],
    headEulerAngleX: 2,
    headEulerAngleY: -12,
    headEulerAngleZ: 4,
  }],
  2,
  3,
);
assert(numeric.length === 1, "numeric Android face maps");
assert(numeric[0].x === 20 && numeric[0].height === 240, "box scales to source coordinates");
assert(numeric[0].landmarks?.leftEye.x === 140, "Android LEFT_EYE=4 maps and scales");
assert(numeric[0].landmarks?.rightEye.x === 60, "Android RIGHT_EYE=10 maps and scales");
assert(numeric[0].landmarks?.noseBase?.y === 165, "Android NOSE_BASE=6 maps and scales");
assert(numeric[0].headEulerAngleY === -12, "Euler yaw is preserved");

const ios = mapDetectedFaces([{
  frame: { origin: { x: 0, y: 0 }, size: { x: 100, y: 100 } },
  landmarks: [
    { type: "LeftEye", position: { x: 70, y: 40 } },
    { type: "RightEye", position: { x: 30, y: 40 } },
    { type: "LeftMouth", position: { x: 65, y: 70 } },
    { type: "RightMouth", position: { x: 35, y: 70 } },
  ],
}]);
assert(ios[0]?.landmarks?.leftMouth.x === 65, "PascalCase iOS landmarks map");

// eslint-disable-next-line no-console
console.log("face-detector self-check passed");
