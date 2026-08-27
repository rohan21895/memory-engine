// @ts-expect-error Node's TypeScript runner requires the source extension.
import { letterboxLayout } from "../ml/movenet.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { KP } from "./pose.ts";
import type { PoseKeypoint } from "./pose";

/**
 * How much of a person the frame actually contains.
 *
 * The owner asked for this directly: "if full torso and head or full body of
 * person is visible or not is known only this way and this will help choose
 * better photos". He is right that pose is the only source — face detection
 * gives a box around a head and says nothing about whether the body below it
 * survived the crop, and the app ships no segmentation model.
 *
 * `pose.ts` already turns the same 17 keypoints into joint ANGLES for grouping
 * near-identical shots. This is the other half: not what shape the body is in,
 * but how much of it is in the picture.
 */

/** Matches `pose.ts`, so a joint counted here is a joint counted there. */
const MIN_KEYPOINT_SCORE = 0.3;

/**
 * How far outside the frame a keypoint may sit and still count as visible.
 *
 * MoveNet regresses coordinates rather than classifying visibility, so a joint
 * just past the edge lands slightly outside rather than vanishing. A hard zero
 * bound would call a shoulder at -0.002 missing and drop the subject a whole
 * tier.
 */
const EDGE_TOLERANCE = 0.02;

/**
 * Distance from the frame edge within which the body is treated as running out
 * of picture rather than ending naturally.
 *
 * Only used to tell "cropped by the frame" apart from "out of shot": legs
 * missing while the hips sit near the bottom edge is a crop; legs missing while
 * the hips sit mid-frame is someone sitting behind a table.
 */
const EDGE_PROXIMITY = 0.08;

/**
 * Tiers, head downward. Order is the whole point: a tier counts as covered only
 * if every tier above it is, so one spuriously confident ankle under a cropped
 * torso cannot promote a head-and-shoulders shot to a full-body one.
 */
const TIERS = [
  { name: "head", points: [KP.nose, KP.l_eye, KP.r_eye, KP.l_ear, KP.r_ear] },
  { name: "shoulders", points: [KP.l_sho, KP.r_sho] },
  { name: "hips", points: [KP.l_hip, KP.r_hip] },
  { name: "knees", points: [KP.l_kne, KP.r_kne] },
  { name: "ankles", points: [KP.l_ank, KP.r_ank] },
] as const;

export type BodyFraming =
  /** Not enough confident keypoints to say anything. Never a reason to reject. */
  | "unknown"
  /** Head only: a face fills the frame, or the body is out of shot. */
  | "head"
  /** Head and shoulders. The classic portrait crop. */
  | "upper"
  /** Down to the hips — "full torso and head", in his words. */
  | "half"
  /** Down to the knees. */
  | "threeQuarter"
  /** Feet in frame. */
  | "full";

export type BodyCoverage = {
  framing: BodyFraming;
  /** Deepest tier fully in frame, head = 0. -1 when nothing is confident. */
  depth: number;
  /**
   * The body is cut off by the edge of the picture rather than simply ending.
   *
   * This is the honest, single-person version of the "no cut body parts" ask.
   * It is advisory: MoveNet fits ONE person, so in a group photo it describes
   * whichever body it locked onto and says nothing about the others.
   */
  cutByFrame: boolean;
  /**
   * The cut lands on a joint rather than between them.
   *
   * Photographers avoid cropping at ankles, knees, wrists and elbows because it
   * reads as an amputation; cropping mid-shin or mid-thigh reads as a choice.
   * Advisory in the same way, and only meaningful when `cutByFrame`.
   */
  cutAtJoint: boolean;
};

type Point = { x: number; y: number; visible: boolean };

/**
 * Put keypoints back into the photo's own coordinates.
 *
 * THE correctness detail in this file. `detectBodyPose` letterboxes the photo
 * into a 192x192 square and returns coordinates normalized to THAT square, with
 * black padding either side. `movenet.ts` notes the keypoints "need no
 * un-padding afterwards" — true for `pose.ts`, which reads only angles, and
 * angles are invariant under the uniform scale plus centring a letterbox
 * applies. It is false for anything positional.
 *
 * Skip this step on a landscape photo and the padding above and below the image
 * reads as picture: a subject standing on the bottom edge appears to float in
 * the middle of the frame, and every crop test silently inverts. The numbers
 * still look plausible, which on this codebase is exactly the dangerous kind.
 */
function toPhotoSpace(
  keypoints: readonly PoseKeypoint[],
  scores: readonly number[],
  sourceWidth: number,
  sourceHeight: number,
): Point[] | undefined {
  let layout: { drawWidth: number; drawHeight: number };
  try {
    layout = letterboxLayout(sourceWidth, sourceHeight);
  } catch {
    return undefined;
  }
  // The square's side cancels: only the drawn fraction and its offset matter.
  const size = Math.max(layout.drawWidth, layout.drawHeight);
  const spanX = layout.drawWidth / size;
  const spanY = layout.drawHeight / size;
  if (spanX <= 0 || spanY <= 0) return undefined;
  const offsetX = (1 - spanX) / 2;
  const offsetY = (1 - spanY) / 2;

  return keypoints.map((point, index) => {
    const x = (point[0] - offsetX) / spanX;
    const y = (point[1] - offsetY) / spanY;
    const confident = (scores[index] ?? 0) >= MIN_KEYPOINT_SCORE;
    return {
      x,
      y,
      visible:
        confident &&
        x >= -EDGE_TOLERANCE &&
        x <= 1 + EDGE_TOLERANCE &&
        y >= -EDGE_TOLERANCE &&
        y <= 1 + EDGE_TOLERANCE,
    };
  });
}

const FRAMINGS: BodyFraming[] = ["head", "upper", "half", "threeQuarter", "full"];

/**
 * What the frame contains of the person MoveNet locked onto.
 *
 * Returns `unknown` rather than guessing whenever the pose is too weak to read.
 * Every consumer must treat `unknown` as "no opinion": most photos in a family
 * library have no clean single subject, and a signal that defaults to a verdict
 * would quietly reweigh the whole album.
 */
export function bodyCoverage(
  keypoints: readonly PoseKeypoint[],
  scores: readonly number[],
  sourceWidth: number,
  sourceHeight: number,
): BodyCoverage {
  const none: BodyCoverage = {
    framing: "unknown",
    depth: -1,
    cutByFrame: false,
    cutAtJoint: false,
  };
  if (keypoints.length < 17 || scores.length < 17) return none;
  const points = toPhotoSpace(keypoints, scores, sourceWidth, sourceHeight);
  if (!points) return none;

  // A tier is covered when at least one of its points is in frame. One of two,
  // not both: a head turned in profile hides an ear, and a shoulder can fall
  // behind another person, neither of which crops the body.
  const covered = TIERS.map((tier) =>
    tier.points.some((index) => points[index]?.visible === true),
  );
  if (!covered[0] && !covered[1]) return none;

  // Deepest tier with an unbroken chain above it.
  let depth = -1;
  for (let index = 0; index < covered.length; index += 1) {
    if (!covered[index]) break;
    depth = index;
  }
  if (depth < 0) return none;

  const framing = FRAMINGS[depth];
  const nextTier = TIERS[depth + 1];

  // Nothing below is missing, so nothing was cut away.
  if (nextTier === undefined) {
    return { framing, depth, cutByFrame: false, cutAtJoint: false };
  }

  // Missing lower body is only a CROP if the body was heading out of frame.
  // The lowest visible point standing near the bottom edge says the picture ran
  // out; the same point mid-frame says the legs are behind a sofa.
  const lowest = points
    .filter((point) => point.visible)
    .reduce((deepest, point) => (point.y > deepest.y ? point : deepest), {
      x: 0,
      y: Number.NEGATIVE_INFINITY,
      visible: true,
    });
  const cutByFrame =
    Number.isFinite(lowest.y) && lowest.y >= 1 - EDGE_PROXIMITY;

  // Where the crop LANDS, not which tier is missing.
  //
  // A frame ending between joints reads as a choice — below the shoulders is a
  // half-length portrait, mid-thigh is a cowboy shot. A frame passing straight
  // through the hip, knee or ankle line reads as an amputation. So this asks
  // whether the deepest visible joint is itself sitting on the edge, and only
  // for the lower body: cropping at the shoulder line is the single most
  // common portrait in anyone's library and must never be marked a defect.
  const deepest = TIERS[depth];
  const jointOnEdge = deepest.points.some((index) => {
    const point = points[index];
    return point?.visible === true && point.y >= 1 - EDGE_PROXIMITY;
  });
  const LOWER_BODY = 2; // hips
  const cutAtJoint = cutByFrame && jointOnEdge && depth >= LOWER_BODY;

  return { framing, depth, cutByFrame, cutAtJoint };
}

/**
 * Order two shots of the same moment by how completely they hold the subject.
 *
 * Negative means `left` is better framed. Deliberately a COMPARATOR and not a
 * score: it is for breaking ties inside a take, where the photos are already
 * near-identical, and it must never be summed into a quality number that could
 * push an otherwise good photo out of an album. CX-19 measured every hard
 * framing gate on this codebase and each one cost real selections.
 *
 * `unknown` compares equal to everything, so a photo the model could not read
 * is never penalised for it.
 */
export function compareFramingCompleteness(
  left: BodyCoverage,
  right: BodyCoverage,
): number {
  if (left.framing === "unknown" || right.framing === "unknown") return 0;
  // An intact subject beats a cropped one before depth is considered: a clean
  // head-and-shoulders portrait is a better picture than a full body sliced off
  // at the ankles, even though the second reaches a deeper tier.
  if (left.cutAtJoint !== right.cutAtJoint) return left.cutAtJoint ? 1 : -1;
  if (left.depth !== right.depth) return right.depth - left.depth;
  if (left.cutByFrame !== right.cutByFrame) return left.cutByFrame ? 1 : -1;
  return 0;
}
