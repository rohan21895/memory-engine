import type {
  NormalizedBox,
  ReframeKeyframe,
  ReframeTrack,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { RenderVideoError } from "./errors.js";
import { frames } from "./time.js";

/**
 * Reframe keyframes are the vertical-reel core: a crop window in normalised SOURCE
 * coordinates, keyed at SOURCE times so the track survives a re-trim of the clip.
 *
 * Two properties matter more than anything else here.
 *
 * 1. The crop must be DEFINED for every frame the renderer is asked to draw. This module
 *    refuses a track that does not span the segment rather than holding the nearest
 *    keyframe, because a crop that quietly stops tracking is how a subject leaves frame
 *    without anybody being told. `ReframeTrack.fallback` does not cover this: it is about
 *    a tracker failing, and here no tracker ran.
 * 2. Interpolation is evaluated against integer source frame numbers, never seconds.
 */

function fail(detail: string): never {
  throw new RenderVideoError("validation_failed", `The video renderer refused a reframe track: ${detail}`);
}

export interface CropPixels {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface KeyedFrame {
  frame: number;
  crop: NormalizedBox;
  interpolation: "linear" | "smooth" | "bezier" | "hold";
  bezier: readonly [{ x: number; y: number }, { x: number; y: number }] | null;
}

export function keyframesAt(track: ReframeTrack, rate: number): KeyedFrame[] {
  const keyed = track.keyframes.map((keyframe: ReframeKeyframe, index) => {
    const control = keyframe.bezier_control ?? null;
    return {
      frame: frames(keyframe.time, rate, `reframe ${track.reframe_track_id} keyframe[${index}] time`),
      crop: keyframe.crop,
      interpolation: keyframe.interpolation ?? "smooth",
      bezier: control && control.length === 2 ? ([control[0]!, control[1]!] as const) : null,
    };
  });
  for (let index = 1; index < keyed.length; index += 1) {
    if (keyed[index]!.frame <= keyed[index - 1]!.frame) {
      fail(`${track.reframe_track_id} keyframes are not strictly increasing in time.`);
    }
  }
  return keyed;
}

/**
 * Cubic Bezier easing with P0=(0,0) and P3=(1,1), as CSS defines it: the control points
 * are (time, value) in normalised keyframe-interval space, so reaching the value needs the
 * time parameter solved for first. Newton with a bisection fallback, fixed iteration
 * counts, no early exit — the same input gives the same bits on every machine.
 */
function bezierEase(u: number, p1: { x: number; y: number }, p2: { x: number; y: number }): number {
  const curveX = (t: number): number => 3 * (1 - t) * (1 - t) * t * p1.x + 3 * (1 - t) * t * t * p2.x + t * t * t;
  const curveY = (t: number): number => 3 * (1 - t) * (1 - t) * t * p1.y + 3 * (1 - t) * t * t * p2.y + t * t * t;
  const slopeX = (t: number): number =>
    3 * (1 - t) * (1 - t) * p1.x + 6 * (1 - t) * t * (p2.x - p1.x) + 3 * t * t * (1 - p2.x);

  let t = u;
  for (let iteration = 0; iteration < 8; iteration += 1) {
    const slope = slopeX(t);
    if (slope === 0) break;
    t -= (curveX(t) - u) / slope;
  }
  if (!(t >= 0) || !(t <= 1)) {
    let low = 0;
    let high = 1;
    t = u;
    for (let iteration = 0; iteration < 40; iteration += 1) {
      t = (low + high) / 2;
      if (curveX(t) < u) low = t;
      else high = t;
    }
  }
  return curveY(t);
}

function mix(from: number, to: number, fraction: number): number {
  return from + (to - from) * fraction;
}

/**
 * `smooth` is a UNIFORM Catmull-Rom spline through the keyframe values with the endpoints
 * clamped, which is what edl.schema.json's ReframeKeyframe.interpolation $comment states
 * (contracts#51). Written exactly as the contract writes it, in the same order, because
 * this is the one interpolation the planner emits for every keyframe of every reel.
 *
 * Uniform, not centripetal: the uniform form uses only +, - and *, so every conforming
 * implementation lands on the same IEEE-754 double. The centripetal variant needs a fourth
 * root, and pow() is not bit-identical across libms — which would make the crop window
 * machine-dependent, and `edl_id` promises it is not.
 */
function catmullRom(a: number, b: number, c: number, d: number, u: number): number {
  const u2 = u * u;
  const u3 = u2 * u;
  return (
    0.5 *
    (2 * b + (-a + c) * u + (2 * a - 5 * b + 4 * c - d) * u2 + (-a + 3 * b - 3 * c + d) * u3)
  );
}

/** The crop window, in normalised coordinates, at one absolute source frame. */
export function cropAt(track: ReframeTrack, keyed: readonly KeyedFrame[], sourceFrame: number): NormalizedBox {
  const first = keyed[0]!;
  const last = keyed[keyed.length - 1]!;
  if (sourceFrame < first.frame || sourceFrame > last.frame) {
    fail(
      `${track.reframe_track_id} covers source frames [${first.frame}, ${last.frame}] and the ` +
        `render needs frame ${sourceFrame}. A crop this worker cannot evaluate is a hard failure, ` +
        "not a centre crop.",
    );
  }

  let index = 0;
  while (index + 1 < keyed.length && keyed[index + 1]!.frame <= sourceFrame) index += 1;
  const from = keyed[index]!;
  if (index + 1 >= keyed.length) return from.crop;
  const to = keyed[index + 1]!;

  const span = to.frame - from.frame;
  const raw = (sourceFrame - from.frame) / span;

  if (from.interpolation === "smooth") {
    // Endpoints clamped: the keyframe before the first is the first, and the keyframe after
    // the last is the last. That is what makes a two-keyframe track degenerate to a straight
    // line instead of needing a special case.
    const before = keyed[index - 1] ?? from;
    const after = keyed[index + 2] ?? to;
    return {
      x: catmullRom(before.crop.x, from.crop.x, to.crop.x, after.crop.x, raw),
      y: catmullRom(before.crop.y, from.crop.y, to.crop.y, after.crop.y, raw),
      // Every component is interpolated by the same formula; for a constant w and h the
      // spline evaluates to that constant, and a crop whose size changes is refused in
      // planCrop rather than resampled here.
      w: from.crop.w,
      h: from.crop.h,
      rotation_deg: 0,
    };
  }

  let fraction: number;
  switch (from.interpolation) {
    case "hold":
      fraction = 0;
      break;
    case "linear":
      fraction = raw;
      break;
    case "bezier":
      if (!from.bezier) fail(`${track.reframe_track_id} declares bezier interpolation with no control points.`);
      fraction = bezierEase(raw, from.bezier[0], from.bezier[1]);
      break;
    default:
      fail(
        `${track.reframe_track_id} uses "${from.interpolation}" interpolation, which the contract ` +
          "does not define.",
      );
  }

  return {
    x: mix(from.crop.x, to.crop.x, fraction),
    y: mix(from.crop.y, to.crop.y, fraction),
    w: from.crop.w,
    h: from.crop.h,
    rotation_deg: 0,
  };
}

export interface CropTrackPlan {
  /** Constant pixel width and height; ffmpeg's crop filter fixes these at configuration time. */
  width: number;
  height: number;
  /** Per-frame x and y, indexed from 0 at the segment's first frame. */
  x: number[];
  y: number[];
}

/**
 * Resolves a segment's crop to whole pixels. Normalised coordinates are relative to the
 * oriented source, so the conversion is one multiply and one round per axis; the crop is
 * then clamped inside the frame so a keyframe that rounds a pixel over the edge fails
 * ffmpeg's own bounds check rather than shifting the whole window.
 */
export function planCrop(
  track: ReframeTrack,
  rate: number,
  sourceStartFrame: number,
  length: number,
  sourceWidth: number,
  sourceHeight: number,
): CropTrackPlan {
  const keyed = keyframesAt(track, rate);
  const first = keyed[0]!;

  const width = Math.round(first.crop.w * sourceWidth);
  const height = Math.round(first.crop.h * sourceHeight);
  if (width < 1 || height < 1 || width > sourceWidth || height > sourceHeight) {
    fail(
      `${track.reframe_track_id} resolves to a ${width}x${height} crop of a ` +
        `${sourceWidth}x${sourceHeight} source.`,
    );
  }

  const x: number[] = [];
  const y: number[] = [];
  for (let offset = 0; offset < length; offset += 1) {
    const crop = cropAt(track, keyed, sourceStartFrame + offset);
    if (crop.w !== first.crop.w || crop.h !== first.crop.h) {
      fail(`${track.reframe_track_id} changes crop size mid-track; this worker renders a fixed crop size.`);
    }
    const px = Math.round(crop.x * sourceWidth);
    const py = Math.round(crop.y * sourceHeight);
    if (px < 0 || py < 0 || px + width > sourceWidth || py + height > sourceHeight) {
      fail(
        `${track.reframe_track_id} places a ${width}x${height} crop at (${px}, ${py}) in a ` +
          `${sourceWidth}x${sourceHeight} source, which falls outside the frame.`,
      );
    }
    x.push(px);
    y.push(py);
  }

  return { width, height, x, y };
}

/**
 * A per-frame value as an ffmpeg expression over `n`, the frame counter the crop filter
 * exposes under `eval=frame`. Runs of one repeated value collapse, so a static crop is a
 * constant and a two-keyframe linear move is a handful of terms rather than one per frame.
 */
export function frameSeriesExpression(values: readonly number[]): string {
  if (values.length === 0) return "0";
  const runs: { from: number; value: number }[] = [];
  values.forEach((value, index) => {
    const last = runs[runs.length - 1];
    if (!last || last.value !== value) runs.push({ from: index, value });
  });
  if (runs.length === 1) return String(runs[0]!.value);

  let expression = String(runs[runs.length - 1]!.value);
  for (let index = runs.length - 2; index >= 0; index -= 1) {
    const next = runs[index + 1]!;
    expression = `if(lt(n,${next.from}),${runs[index]!.value},${expression})`;
  }
  return expression;
}
