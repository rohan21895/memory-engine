import type { RationalTime, TimeRange } from "../../../contracts/codegen/generated/typescript/index.js";

import { RenderVideoError } from "./errors.js";

/**
 * Every position this worker computes is an integer count of timeline frames.
 *
 * The contract stores time as RationalTime {value, rate} precisely so that 30000/1001
 * never has to be represented as a float number of seconds. If the renderer converted to
 * seconds to do arithmetic and back to frames to seek, a 15 second reel at 59.94 would
 * accumulate enough error to move a downbeat cut by a frame, and nothing would report it.
 * So: frames in, integer arithmetic, and a single conversion to seconds or samples at the
 * ffmpeg boundary, always from an absolute frame index and never from an accumulator.
 */

function fail(detail: string): never {
  throw new RenderVideoError("validation_failed", `EDL time is not renderable: ${detail}`);
}

/** True when two declared rates are the same number. Rates are contract data, not measurements. */
export function sameRate(left: number, right: number): boolean {
  return left === right;
}

/**
 * A RationalTime as an exact integer frame count at the timeline rate.
 *
 * Refuses a different rate rather than rescaling: rescaling 48000 Hz samples onto a 59.94
 * frame grid is lossy, and the contract only permits a mixed rate on audio tracks, where
 * this worker requires the timeline rate as well (see gate.ts).
 */
export function frames(time: RationalTime, rate: number, what: string): number {
  if (!Number.isFinite(time.value) || !Number.isFinite(time.rate)) fail(`${what} is not finite.`);
  if (!sameRate(time.rate, rate)) {
    fail(`${what} is at rate ${time.rate} but the timeline is at ${rate}.`);
  }
  if (!Number.isInteger(time.value)) {
    fail(`${what} is ${time.value}, which is not a whole frame at ${rate}.`);
  }
  return time.value;
}

/** A RationalTime that is allowed to fall between frames, used only for beat comparison. */
export function fractionalFrames(time: RationalTime, rate: number, what: string): number {
  if (!Number.isFinite(time.value) || !Number.isFinite(time.rate)) fail(`${what} is not finite.`);
  if (!sameRate(time.rate, rate)) {
    fail(`${what} is at rate ${time.rate} but the timeline is at ${rate}.`);
  }
  return time.value;
}

export interface FrameRange {
  /** Inclusive first frame. */
  start: number;
  /** Exclusive last frame; the contract's TimeRange is half-open and so is this. */
  end: number;
}

export function frameRange(range: TimeRange, rate: number, what: string): FrameRange {
  const start = frames(range.start_time, rate, `${what} start_time`);
  const duration = frames(range.duration, rate, `${what} duration`);
  if (duration <= 0) fail(`${what} has a duration of ${duration} frames.`);
  return { start, end: start + duration };
}

export function rangeLength(range: FrameRange): number {
  return range.end - range.start;
}

export function contains(outer: FrameRange, inner: FrameRange): boolean {
  return inner.start >= outer.start && inner.end <= outer.end;
}

/**
 * Frames to seconds, for the one place it is unavoidable: an ffmpeg argument. Always call
 * this on an absolute frame index so the error is bounded by one conversion rather than
 * accumulating across a timeline.
 */
export function framesToSeconds(frameCount: number, rate: number): number {
  return frameCount / rate;
}

/** Frames to whole audio samples, rounded once, from an absolute frame index. */
export function framesToSamples(frameCount: number, rate: number, sampleRate: number): number {
  return Math.round((frameCount * sampleRate) / rate);
}

export function framesToMilliseconds(frameCount: number, rate: number): number {
  return (frameCount * 1000) / rate;
}

/**
 * A fixed-width decimal for an ffmpeg time argument. ffmpeg parses these as doubles, so
 * the digits are what pins the value; the same frame index always yields the same string,
 * which is what keeps two renders of one EDL byte-identical.
 */
export function secondsArg(seconds: number): string {
  return seconds.toFixed(9);
}
