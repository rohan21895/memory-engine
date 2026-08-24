// @ts-expect-error Node's native TypeScript runner requires the extension.
import { exposureFromPixels, sharpnessFromPixels } from "./image-quality.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`Image-quality self-check failed: ${message}`);
  }
}

const width = 32;
const height = 32;
const crispEdges = Uint8Array.from(
  { length: width * height },
  (_, index) => (index % width) % 2 === 0 ? 0 : 255,
);
assert(
  sharpnessFromPixels(crispEdges, width, height) > 0.9,
  "alternating crisp edges should score near full sharpness",
);

const flatGray = new Uint8Array(width * height).fill(128);
assert(
  sharpnessFromPixels(flatGray, width, height) === 0,
  "a flat buffer should have zero Laplacian variance",
);

const softGradient = Uint8Array.from(
  { length: width * height },
  (_, index) => Math.round(((index % width) / (width - 1)) * 255),
);
assert(
  sharpnessFromPixels(softGradient, width, height) < 0.01,
  "a smooth gradient should remain near zero sharpness",
);

const allWhite = new Uint8Array(width * height).fill(255);
const whiteExposure = exposureFromPixels(allWhite, width, height);
assert(whiteExposure.exposure > 0.99, "white should have high exposure");
assert(
  whiteExposure.clippedFraction === 1,
  "every white pixel should count as clipped",
);

const middleGray = exposureFromPixels(flatGray, width, height);
assert(
  Math.abs(middleGray.exposure - 128 / 255) < 1e-9,
  "exposure should be normalized mean luma",
);
assert(
  middleGray.clippedFraction === 0,
  "middle gray should contain no clipped pixels",
);
