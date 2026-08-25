// @ts-expect-error Node's TypeScript runner requires the source extension.
import { mapDetectedFaces, scaleFaceBox } from "./face-detector.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { landmarksToPatch, patchGeometry } from "../ml/face-crop.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-detector self-check failed: ${message}`);
}

function close(actual: number, expected: number, message: string): void {
  assert(
    Math.abs(actual - expected) < 1e-9,
    `${message}: got ${actual}, want ${expected}`,
  );
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
assert(ios[0]?.landmarks?.leftMouth?.x === 65, "PascalCase iOS landmarks map");

// The scan detects in source coordinates and then crops from a downscaled
// shared frame, so every face makes one round trip through scaleFaceBox. A
// rescale that misses the landmarks (or the image dimensions) produces an
// alignment that is wrong by a constant offset: no error, no visible artifact,
// and every identity in the library quietly degraded. These assertions are the
// only thing standing between that bug and a 100-minute rescan.
{
  const source = { width: 4032, height: 3024 };
  const frameScale = 1280 / 4032;
  const frame = {
    width: source.width * frameScale,
    height: source.height * frameScale,
  };
  const box = {
    x: 1500,
    y: 900,
    width: 420,
    height: 460,
    landmarks: {
      leftEye: { x: 1810, y: 1030 },
      rightEye: { x: 1620, y: 1035 },
      noseBase: { x: 1712, y: 1130 },
      leftMouth: { x: 1790, y: 1245 },
      rightMouth: { x: 1645, y: 1250 },
    },
  };

  const framedBox = scaleFaceBox(box, frameScale);
  assert(framedBox.landmarks !== undefined, "rescaling keeps the landmarks");
  close(framedBox.width, box.width * frameScale, "the box width rescales");
  close(
    framedBox.landmarks.leftEye.x,
    box.landmarks.leftEye.x * frameScale,
    "the landmarks rescale with the box",
  );

  const sourceGeometry = patchGeometry(source, box);
  const framedGeometry = patchGeometry(frame, framedBox);
  assert(sourceGeometry !== undefined, "the source patch geometry resolves");
  assert(framedGeometry !== undefined, "the framed patch geometry resolves");

  const sourcePatch = landmarksToPatch(box.landmarks, sourceGeometry);
  const framedPatch = landmarksToPatch(framedBox.landmarks, framedGeometry);
  for (const name of ["leftEye", "rightEye", "noseBase", "leftMouth", "rightMouth"] as const) {
    const expected = sourcePatch[name];
    const actual = framedPatch[name];
    assert(expected !== undefined && actual !== undefined, `${name} survives`);
    close(actual.x, expected.x, `${name} lands on the same patch column`);
    close(actual.y, expected.y, `${name} lands on the same patch row`);
  }

  // Negative control: scaling the box but leaving the landmarks in source
  // coordinates must NOT agree, or the assertions above prove nothing.
  const halfScaled = { ...framedBox, landmarks: box.landmarks };
  const wrongGeometry = patchGeometry(frame, halfScaled);
  assert(wrongGeometry !== undefined, "the mismatched geometry still resolves");
  const wrongPatch = landmarksToPatch(box.landmarks, wrongGeometry);
  assert(
    Math.abs(wrongPatch.leftEye.x - sourcePatch.leftEye.x) > 1,
    "forgetting to rescale the landmarks must be detectable",
  );

  // Detection reports source coordinates by scaling the frame boxes up by
  // 1/scale, and the crop scales them back down. That round trip is exact.
  const roundTripped = scaleFaceBox(scaleFaceBox(box, 1 / frameScale), frameScale);
  close(roundTripped.x, box.x, "the detect/crop round trip preserves the box");
  assert(roundTripped.landmarks !== undefined, "the round trip keeps landmarks");
  close(
    roundTripped.landmarks.leftEye.y,
    box.landmarks.leftEye.y,
    "the detect/crop round trip preserves the landmarks",
  );

  assert(scaleFaceBox(box, 1) === box, "an identity rescale allocates nothing");
  assert(scaleFaceBox(box, 0) === box, "a degenerate scale is refused");
  assert(
    scaleFaceBox(box, Number.NaN) === box,
    "a non-finite scale is refused",
  );
}

// eslint-disable-next-line no-console
console.log("face-detector self-check passed");

// ── Eyes alone are enough, and must NOT fall back to an unaligned crop ──
// ML Kit routinely reports an off-frontal face with both eyes but only one
// mouth corner, or none. Requiring all four corners rejected those faces, and
// every rejection silently took the bounding-box path — which is what stops
// ArcFace embeddings separating people. The aligner degrades on its own, so the
// detector must hand it whatever it has.
const eyesOnly = mapDetectedFaces([{
  frame: { origin: { x: 0, y: 0 }, size: { x: 100, y: 100 } },
  landmarks: [
    { type: "LeftEye", position: { x: 70, y: 40 } },
    { type: "RightEye", position: { x: 30, y: 40 } },
  ],
}]);
assert(eyesOnly[0]?.landmarks, "a face with only eyes still carries landmarks");
assert(eyesOnly[0].landmarks.leftMouth === undefined, "an absent mouth corner stays absent");
assert(eyesOnly[0].landmarks.rightMouth === undefined, "an absent mouth corner is never invented");

// One mouth corner is not enough for the 4-point fit, but must not discard the
// eyes: the aligner will simply use a lower tier.
const oneCorner = mapDetectedFaces([{
  frame: { origin: { x: 0, y: 0 }, size: { x: 100, y: 100 } },
  landmarks: [
    { type: "LeftEye", position: { x: 70, y: 40 } },
    { type: "RightEye", position: { x: 30, y: 40 } },
    { type: "LeftMouth", position: { x: 65, y: 70 } },
  ],
}]);
assert(oneCorner[0]?.landmarks?.leftEye, "one mouth corner still keeps the eyes");

// No eyes is still a genuine rejection — two points are the minimum a
// similarity transform needs.
const noEyes = mapDetectedFaces([{
  frame: { origin: { x: 0, y: 0 }, size: { x: 100, y: 100 } },
  landmarks: [{ type: "LeftMouth", position: { x: 65, y: 70 } }],
}]);
assert(noEyes[0]?.landmarks === undefined, "a face with no eyes has no usable landmarks");
