import {
  bodyCoverage,
  compareFramingCompleteness,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./pose-framing.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { KP, letterboxLayout } from "./pose-framing-test-deps.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`pose-framing self-check failed: ${message}`);
}

/**
 * The owner asked for this: "if full torso and head or full body of person is
 * visible or not is known only this way and this will help choose better
 * photos."
 *
 * Everything here is really one question — does a keypoint land where the
 * person actually is in the picture? MoveNet returns coordinates in a
 * letterboxed 192x192 square, so a landscape photo occupies only the middle
 * 75% of the vertical range and the rest is black padding. Read those raw and
 * a subject standing on the bottom edge looks like they are floating
 * mid-frame. Nothing errors; the numbers just quietly mean something else.
 */

const LANDSCAPE = { width: 4000, height: 3000 };
const PORTRAIT = { width: 3000, height: 4000 };
const SQUARE = { width: 2000, height: 2000 };

/** Photo-space [0,1] -> the letterboxed square MoveNet actually reports in. */
function toModelSpace(
  x: number,
  y: number,
  source: { width: number; height: number },
): readonly [number, number] {
  const { drawWidth, drawHeight } = letterboxLayout(source.width, source.height);
  const size = Math.max(drawWidth, drawHeight);
  const spanX = drawWidth / size;
  const spanY = drawHeight / size;
  return [x * spanX + (1 - spanX) / 2, y * spanY + (1 - spanY) / 2] as const;
}

/**
 * Build a pose from photo-space positions, so every fixture reads in the
 * coordinates a human thinks in and the letterbox is applied for us.
 */
function pose(
  visible: Partial<Record<keyof typeof KP, readonly [number, number]>>,
  source: { width: number; height: number },
) {
  const keypoints: Array<readonly [number, number]> = [];
  const scores: number[] = [];
  for (const name of Object.keys(KP) as Array<keyof typeof KP>) {
    const at = visible[name];
    if (at) {
      keypoints[KP[name]] = toModelSpace(at[0], at[1], source);
      scores[KP[name]] = 0.9;
    } else {
      // Absent joints still come back with coordinates; it is the SCORE that
      // marks them unusable. Parking them at a plausible spot keeps the test
      // honest about which signal is doing the work.
      keypoints[KP[name]] = toModelSpace(0.5, 0.5, source);
      scores[KP[name]] = 0.05;
    }
  }
  return { keypoints, scores };
}

const HEAD = { nose: [0.5, 0.1], l_eye: [0.46, 0.08], r_eye: [0.54, 0.08] } as const;
const SHOULDERS = { l_sho: [0.4, 0.25], r_sho: [0.6, 0.25] } as const;
const HIPS = { l_hip: [0.42, 0.5], r_hip: [0.58, 0.5] } as const;
const KNEES = { l_kne: [0.43, 0.7], r_kne: [0.57, 0.7] } as const;
const ANKLES = { l_ank: [0.44, 0.9], r_ank: [0.56, 0.9] } as const;

const coverageOf = (
  parts: Record<string, readonly [number, number]>,
  source = LANDSCAPE,
) => {
  const { keypoints, scores } = pose(parts as never, source);
  return bodyCoverage(keypoints, scores, source.width, source.height);
};

// The five tiers, read in the owner's terms.
{
  assert(coverageOf({ ...HEAD }).framing === "head", "head alone is a head shot");
  assert(
    coverageOf({ ...HEAD, ...SHOULDERS }).framing === "upper",
    "head and shoulders is a portrait crop",
  );
  const torso = coverageOf({ ...HEAD, ...SHOULDERS, ...HIPS });
  assert(
    torso.framing === "half",
    `"full torso and head" is the hips tier, got ${torso.framing}`,
  );
  assert(
    coverageOf({ ...HEAD, ...SHOULDERS, ...HIPS, ...KNEES }).framing === "threeQuarter",
    "down to the knees",
  );
  const whole = coverageOf({ ...HEAD, ...SHOULDERS, ...HIPS, ...KNEES, ...ANKLES });
  assert(whole.framing === "full", `feet in frame is a full body, got ${whole.framing}`);
  assert(!whole.cutByFrame, "a whole body inside the frame is not cropped");
}

/**
 * THE case this file exists for.
 *
 * Identical body, three aspect ratios. Read without un-letterboxing, the
 * landscape and portrait answers diverge, because the same photo-space y maps
 * to a different square-space y in each.
 */
{
  const parts = { ...HEAD, ...SHOULDERS, ...HIPS, ...KNEES, ...ANKLES };
  const shapes = [
    ["landscape", LANDSCAPE],
    ["portrait", PORTRAIT],
    ["square", SQUARE],
  ] as const;
  for (const [label, source] of shapes) {
    const got = coverageOf(parts, source);
    assert(
      got.framing === "full",
      `${label} must read as a full body like every other shape, got ${got.framing}`,
    );
    assert(!got.cutByFrame, `${label} must not report a crop`);
  }
  // Vacuity guard: the shapes really do letterbox differently, so agreeing
  // above is a property of the un-padding and not of three identical inputs.
  const land = letterboxLayout(LANDSCAPE.width, LANDSCAPE.height);
  const port = letterboxLayout(PORTRAIT.width, PORTRAIT.height);
  assert(
    land.drawHeight !== port.drawHeight && land.drawWidth !== port.drawWidth,
    `the fixtures must letterbox differently or this proves nothing ` +
      `(landscape ${land.drawWidth}x${land.drawHeight}, portrait ${port.drawWidth}x${port.drawHeight})`,
  );
  // And concretely: on a 4000x3000 photo the bottom edge sits at 0.875 of the
  // square, not 1.0. Anything reading raw coordinates believes a subject on the
  // bottom edge is floating in open space.
  const bottom = toModelSpace(0.5, 1, LANDSCAPE)[1];
  assert(
    Math.abs(bottom - 0.875) < 0.01,
    `the landscape bottom edge must land at 0.875 in model space, got ${bottom.toFixed(3)}`,
  );
}

// Cropped by the frame vs simply out of shot. Same missing legs, different
// cause, and only one of them is a worse photograph.
{
  const cropped = coverageOf({
    ...HEAD,
    ...SHOULDERS,
    l_hip: [0.42, 0.96],
    r_hip: [0.58, 0.96],
  });
  assert(cropped.framing === "half", "still a torso shot");
  assert(cropped.cutByFrame, "hips against the bottom edge means the picture ran out");
  assert(cropped.cutAtJoint, "and the hips are a joint, so the cut reads as severed");

  const seated = coverageOf({ ...HEAD, ...SHOULDERS, ...HIPS });
  assert(seated.framing === "half", "same tier");
  assert(
    !seated.cutByFrame,
    "hips at mid-frame means the legs are behind something, not cut off",
  );
}

// Head-and-shoulders is a deliberate portrait, not an amputation.
{
  const portrait = coverageOf({
    ...HEAD,
    l_sho: [0.4, 0.95],
    r_sho: [0.6, 0.95],
  });
  assert(portrait.cutByFrame, "the frame does end below the shoulders");
  assert(
    !portrait.cutAtJoint,
    "but shoulders crop cleanly — this is the standard portrait, not a defect",
  );
}

// An unreadable pose must produce no opinion at all. Most photos in a family
// library have no clean single subject, and a signal that defaulted to a
// verdict would silently reweigh the whole album.
{
  const { keypoints, scores } = pose({}, LANDSCAPE);
  const got = bodyCoverage(keypoints, scores, LANDSCAPE.width, LANDSCAPE.height);
  assert(got.framing === "unknown", `no confident joints means no opinion, got ${got.framing}`);
  assert(got.depth === -1 && !got.cutByFrame, "and no derived claims either");
  assert(
    bodyCoverage([], [], LANDSCAPE.width, LANDSCAPE.height).framing === "unknown",
    "an empty pose is unknown, not a crash",
  );
  assert(
    bodyCoverage(keypoints, scores, 0, 0).framing === "unknown",
    "impossible dimensions are unknown, not a crash",
  );
}

// A stray confident ankle under a cropped torso must not promote the shot to a
// full body. The chain has to be unbroken.
{
  const got = coverageOf({ ...HEAD, ...SHOULDERS, ...ANKLES });
  assert(
    got.framing === "upper",
    `a gap in the chain stops the count at the break, got ${got.framing}`,
  );
}

// A joint just outside the frame edge still counts: MoveNet regresses
// coordinates rather than classifying visibility, so a shoulder at -0.002 is a
// visible shoulder, not a missing one.
{
  const got = coverageOf({
    ...HEAD,
    l_sho: [-0.005, 0.25],
    r_sho: [0.6, 0.25],
  });
  assert(got.framing === "upper", `a joint barely off-edge is still visible, got ${got.framing}`);
  // Vacuity guard: far outside really is excluded, so the tolerance is a
  // tolerance and not an unconditional pass.
  const far = coverageOf({ ...HEAD, l_sho: [-0.6, 0.25], r_sho: [1.7, 0.25] });
  assert(
    far.framing === "head",
    `joints well outside the frame must not count, got ${far.framing}`,
  );
}

/**
 * The comparator. It only ever breaks ties inside a take, so the ordering it
 * imposes matters more than any score.
 */
{
  const full = coverageOf({ ...HEAD, ...SHOULDERS, ...HIPS, ...KNEES, ...ANKLES });
  const upper = coverageOf({ ...HEAD, ...SHOULDERS });
  assert(
    compareFramingCompleteness(full, upper) < 0,
    "more of the person in frame wins when neither is cut",
  );

  // An intact portrait beats a full body sliced at the ankles, even though the
  // sliced one reaches a deeper tier. This is the whole reason cutAtJoint is
  // checked before depth.
  const slicedAtAnkles = coverageOf({
    ...HEAD,
    ...SHOULDERS,
    ...HIPS,
    l_kne: [0.43, 0.97],
    r_kne: [0.57, 0.97],
  });
  assert(slicedAtAnkles.cutAtJoint, "the fixture must actually be cut at a joint");
  assert(
    compareFramingCompleteness(upper, slicedAtAnkles) < 0,
    "a clean portrait beats a body cut off at the knees",
  );

  // Unknown is inert in both directions — never better, never worse.
  const unknown = bodyCoverage([], [], LANDSCAPE.width, LANDSCAPE.height);
  assert(
    compareFramingCompleteness(unknown, full) === 0 &&
      compareFramingCompleteness(full, unknown) === 0,
    "a pose the model could not read must not be penalised or rewarded",
  );

  // Sorting with it must be stable and total, or a tie-break becomes a shuffle.
  assert(
    compareFramingCompleteness(full, full) === 0,
    "a photo compares equal to itself",
  );
  assert(
    compareFramingCompleteness(upper, full) > 0,
    "and the comparator is antisymmetric",
  );
}

console.log("pose-framing self-check passed");
