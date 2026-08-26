// @ts-expect-error Node's native TypeScript runner requires the extension.
import { exposureFromPixels, relativeQualityFloor, sharpnessFromPixels, subjectQualityFromPixels } from "./image-quality.ts";

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
// Threshold widened from 0.01 when normalization moved from variance/20000 to
// sqrt(variance)/50: this buffer measures 0.0137 rather than 0.000023. Still
// "near zero" in the sense that matters — the blurriest real photo measured in
// calibration sits at 0.07, five times higher.
assert(
  sharpnessFromPixels(softGradient, width, height) < 0.02,
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

// A shallow-depth-of-field portrait in miniature: crisp subject in the middle,
// smooth out-of-focus surroundings. Globally this looks soft; only measuring
// inside the subject box recovers the fact that the subject is tack sharp.
const bokehSize = 64;
const subjectBox = { x: 24, y: 24, width: 16, height: 16 };
const bokeh = Uint8Array.from({ length: bokehSize * bokehSize }, (_, index) => {
  const x = index % bokehSize;
  const y = Math.floor(index / bokehSize);
  const inSubject =
    x >= subjectBox.x &&
    x < subjectBox.x + subjectBox.width &&
    y >= subjectBox.y &&
    y < subjectBox.y + subjectBox.height;
  // Low amplitude on purpose: a full-swing checkerboard clamps both scores to
  // 1 and the comparison stops testing anything.
  return inSubject ? ((x + y) % 2 === 0 ? 122 : 134) : Math.round((x / (bokehSize - 1)) * 255);
});

const globalBokeh = sharpnessFromPixels(bokeh, bokehSize, bokehSize);
const subjectBokeh = sharpnessFromPixels(
  bokeh,
  bokehSize,
  bokehSize,
  subjectBox,
);
// Measures 0.26 globally versus 0.98 on the subject. The global value sits
// under the planner's 0.35 quality floor, so this is exactly the shot that
// absolute sharpness throws away and subject-relative sharpness rescues.
assert(
  subjectBokeh > globalBokeh * 2,
  "subject-box sharpness should far exceed the frame average for a bokeh shot",
);
assert(
  subjectBokeh > 0.9,
  "a crisp subject should score near full sharpness inside its own box",
);
assert(
  sharpnessFromPixels(bokeh, bokehSize, bokehSize, {
    x: 0,
    y: 0,
    width: 12,
    height: 12,
  }) < 0.02,
  "a smooth background corner should stay near zero sharpness",
);

// A degenerate or off-image box must not zero the score; it falls back to the
// whole frame so a bad face box can never look like a blurry photo.
assert(
  sharpnessFromPixels(bokeh, bokehSize, bokehSize, {
    x: 0,
    y: 0,
    width: 0,
    height: 0,
  }) === globalBokeh,
  "a degenerate region should fall back to whole-frame sharpness",
);
assert(
  sharpnessFromPixels(crispEdges, width, height) ===
    sharpnessFromPixels(crispEdges, width, height, undefined),
  "omitting the region must match passing undefined",
);

// A tight face box alone cannot see motion-blurred hair, hands or shoulders.
// Keep the face pixels identical and alter only the expanded upper-body region:
// the exact face score must stay fixed while subject sharpness falls.
const portraitWidth = 96;
const portraitHeight = 128;
const portraitFace = { x: 40, y: 16, width: 16, height: 16 };
function portraitFixture(blurUpperBody: boolean): Uint8Array {
  return Uint8Array.from(
    { length: portraitWidth * portraitHeight },
    (_, index) => {
      const x = index % portraitWidth;
      const y = Math.floor(index / portraitWidth);
      const inFace = x >= 40 && x < 56 && y >= 16 && y < 32;
      const inUpperBody = x >= 28 && x < 68 && y >= 9 && y < 84;
      if (inFace) return (x + y) % 2 === 0 ? 124 : 132;
      if (inUpperBody) {
        return blurUpperBody ? 128 : (x + y) % 2 === 0 ? 126 : 130;
      }
      return (x + y) % 2 === 0 ? 122 : 134;
    },
  );
}
const sharpPortrait = subjectQualityFromPixels(
  portraitFixture(false),
  portraitWidth,
  portraitHeight,
  portraitFace,
);
const blurredUpperBody = subjectQualityFromPixels(
  portraitFixture(true),
  portraitWidth,
  portraitHeight,
  portraitFace,
);
console.log(
  `CX-16 regional sharpness measurements ${JSON.stringify({ sharpPortrait, blurredUpperBody })}`,
);
assert(
  Math.abs(
    (sharpPortrait.faceSharpness ?? 0) -
      (blurredUpperBody.faceSharpness ?? 0),
  ) < 0.02,
  "identical face pixels should keep exact-face sharpness within the one-pixel boundary effect",
);
assert(
  (sharpPortrait.subjectSharpness ?? 0) >
    (blurredUpperBody.subjectSharpness ?? 0) * 1.5,
  "blur outside the face but inside hair/upper-body region must lower subject sharpness",
);

// relativeQualityFloor always keeps someone, which is what makes the empty
// album impossible when a consumer swaps its fixed floor for this.
const scores = [0.11, 0.42, 0.19, 0.35, 0.28];
const halfFloor = relativeQualityFloor(scores, 0.5);
assert(
  scores.filter((score) => score >= halfFloor).length >= 3,
  "keepFraction 0.5 of five photos should keep at least three",
);
assert(
  scores.filter((score) => score >= relativeQualityFloor(scores, 0)).length >= 1,
  "even keepFraction 0 must leave one survivor",
);
assert(
  relativeQualityFloor(scores, 1) === 0.11,
  "keepFraction 1 should floor at the worst observed value",
);
assert(
  relativeQualityFloor([undefined, undefined]) === 0,
  "no measurable values should reject nothing",
);
assert(
  relativeQualityFloor([]) === 0,
  "an empty cluster should reject nothing",
);

console.log("image-quality self-checks passed");
