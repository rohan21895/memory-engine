import { describe, expect, it } from "vitest";

import type { ReframeTrack } from "../../../contracts/codegen/generated/typescript/index.js";

import { cropAt, frameSeriesExpression, keyframesAt, planCrop } from "../src/reframe.js";
import { NTSC_30, reframeTrack, t } from "./helpers.js";

function track(interpolation: "linear" | "smooth" | "bezier" | "hold", bezier = false): ReframeTrack {
  return {
    reframe_track_id: "rf",
    target_aspect_ratio: { numerator: 9, denominator: 16 },
    keyframes: [
      {
        time: t(100),
        crop: { x: 0, y: 0, w: 0.5, h: 1, rotation_deg: 0 },
        interpolation,
        bezier_control: bezier ? [{ x: 0.42, y: 0 }, { x: 0.58, y: 1 }] : null,
        confidence: 1,
      },
      {
        time: t(110),
        crop: { x: 0.4, y: 0, w: 0.5, h: 1, rotation_deg: 0 },
        interpolation: "hold",
        bezier_control: null,
        confidence: 1,
      },
    ],
    subject_lock: null,
    smoothing: null,
    fallback: "hold_last_keyframe",
  };
}

describe("reframe keyframe evaluation", () => {
  it("interpolates linearly against integer source frames", () => {
    const definition = track("linear");
    const keyed = keyframesAt(definition, NTSC_30);
    expect(cropAt(definition, keyed, 100).x).toBeCloseTo(0, 10);
    expect(cropAt(definition, keyed, 105).x).toBeCloseTo(0.2, 10);
    expect(cropAt(definition, keyed, 110).x).toBeCloseTo(0.4, 10);
  });

  it("holds until the next keyframe when told to hold", () => {
    const definition = track("hold");
    const keyed = keyframesAt(definition, NTSC_30);
    expect(cropAt(definition, keyed, 105).x).toBe(0);
    expect(cropAt(definition, keyed, 109).x).toBe(0);
    expect(cropAt(definition, keyed, 110).x).toBe(0.4);
  });

  it("solves the bezier for time before reading the value, as CSS defines it", () => {
    const definition = track("bezier", true);
    const keyed = keyframesAt(definition, NTSC_30);
    // A symmetric ease passes through the midpoint at the midpoint and is slower at the ends.
    expect(cropAt(definition, keyed, 105).x).toBeCloseTo(0.2, 6);
    expect(cropAt(definition, keyed, 101).x).toBeLessThan(0.04);
    expect(cropAt(definition, keyed, 109).x).toBeGreaterThan(0.36);
  });

  describe("`smooth` is the clamped uniform Catmull-Rom the contract states (contracts#51)", () => {
    /** The formula from ReframeKeyframe.interpolation's $comment, written independently. */
    function expected(a: number, b: number, c: number, d: number, u: number): number {
      return (
        0.5 *
        (2 * b +
          (-a + c) * u +
          (2 * a - 5 * b + 4 * c - d) * u ** 2 +
          (-a + 3 * b - 3 * c + d) * u ** 3)
      );
    }

    it("is an ease on a two-keyframe track, and is NOT a lerp", () => {
      // With both endpoints clamped, A = B and D = C, and the cubic collapses to
      //   B + (C - B) * (0.5u + 1.5u^2 - u^3)
      // which the schema states explicitly. Half the segment slope at each end is the
      // whole point of clamping, and calling it a straight line would be a plausible
      // description of a curve that is measurably not one.
      const definition = track("smooth");
      const keyed = keyframesAt(definition, NTSC_30);
      const closedForm = (u: number): number => 0.4 * (0.5 * u + 1.5 * u ** 2 - u ** 3);
      for (const frame of [100, 101, 103, 105, 107, 109, 110]) {
        const u = (frame - 100) / 10;
        expect(cropAt(definition, keyed, frame).x).toBeCloseTo(closedForm(u), 12);
      }
      // Through both keyframes, through the midpoint at the midpoint, and slower than a
      // lerp at the ends.
      expect(cropAt(definition, keyed, 100).x).toBeCloseTo(0, 12);
      expect(cropAt(definition, keyed, 105).x).toBeCloseTo(0.2, 12);
      expect(cropAt(definition, keyed, 110).x).toBeCloseTo(0.4, 12);
      expect(cropAt(definition, keyed, 101).x).toBeLessThan(0.04);
      expect(cropAt(definition, keyed, 109).x).toBeGreaterThan(0.36);
    });

    it("stays inside the two keyframes it joins and never goes backwards", () => {
      const definition = track("smooth");
      const keyed = keyframesAt(definition, NTSC_30);
      const path = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110].map(
        (frame) => cropAt(definition, keyed, frame).x,
      );
      path.forEach((value, index) => {
        expect(value).toBeGreaterThanOrEqual(index === 0 ? 0 : path[index - 1]!);
        expect(value).toBeLessThanOrEqual(0.4);
      });
    });

    it("matches the stated formula frame for frame with an interior keyframe", () => {
      const definition = track("smooth");
      definition.keyframes[0]!.interpolation = "smooth";
      definition.keyframes[1]!.interpolation = "smooth";
      definition.keyframes.push({
        time: t(120),
        crop: { x: 0.5, y: 0, w: 0.5, h: 1, rotation_deg: 0 },
        interpolation: "hold",
        bezier_control: null,
        confidence: 1,
      });
      const keyed = keyframesAt(definition, NTSC_30);
      // Second interval: A = 0 (the first keyframe), B = 0.4, C = 0.5, D = 0.5 (clamped).
      for (const frame of [111, 113, 115, 118]) {
        const u = (frame - 110) / 10;
        expect(cropAt(definition, keyed, frame).x).toBeCloseTo(expected(0, 0.4, 0.5, 0.5, u), 12);
      }
      // It passes exactly through its keyframes, which is what makes a spline usable as a
      // plan: the planner's stated positions are the ones rendered.
      expect(cropAt(definition, keyed, 110).x).toBeCloseTo(0.4, 12);
      expect(cropAt(definition, keyed, 120).x).toBeCloseTo(0.5, 12);
    });

    it("carries a constant width and height through unchanged", () => {
      const definition = track("smooth");
      const keyed = keyframesAt(definition, NTSC_30);
      expect(cropAt(definition, keyed, 105).w).toBe(0.5);
      expect(cropAt(definition, keyed, 105).h).toBe(1);
    });
  });

  it("refuses a frame the track does not cover rather than holding the nearest keyframe", () => {
    const definition = track("linear");
    const keyed = keyframesAt(definition, NTSC_30);
    expect(() => cropAt(definition, keyed, 111)).toThrow(/hard failure/);
    expect(() => cropAt(definition, keyed, 99)).toThrow(/hard failure/);
  });

  it("refuses keyframes that are not strictly increasing", () => {
    const definition = track("linear");
    definition.keyframes[1]!.time = t(100);
    expect(() => keyframesAt(definition, NTSC_30)).toThrow(/strictly increasing/);
  });
});

describe("crop planning", () => {
  it("resolves normalised coordinates to whole pixels inside the frame", () => {
    const plan = planCrop(reframeTrack("rf", 100, 200), NTSC_30, 100, 101, 640, 360);
    expect(plan.width).toBe(Math.round(0.31640625 * 640));
    expect(plan.height).toBe(360);
    expect(plan.x).toHaveLength(101);
    expect(plan.x[0]).toBe(Math.round(0.2 * 640));
    expect(plan.x[50]).toBe(Math.round(0.44 * 640));
    expect(plan.x[100]).toBe(Math.round(0.44 * 640));
    expect(Math.max(...plan.x) + plan.width).toBeLessThanOrEqual(640);
  });

  it("refuses a crop that would fall outside the source frame", () => {
    const definition = reframeTrack("rf", 100, 200);
    definition.keyframes[2]!.crop.x = 0.95;
    definition.keyframes[1]!.crop.x = 0.95;
    expect(() => planCrop(definition, NTSC_30, 100, 101, 640, 360)).toThrow(/outside the frame/);
  });
});

describe("per-frame expressions", () => {
  it("collapses a constant to a constant", () => {
    expect(frameSeriesExpression([7, 7, 7, 7])).toBe("7");
  });

  it("emits one branch per value change and keeps them in frame order", () => {
    expect(frameSeriesExpression([1, 1, 2, 3, 3])).toBe("if(lt(n,2),1,if(lt(n,3),2,3))");
  });

  it("evaluates to the source series for every frame", () => {
    const values = [4, 4, 5, 6, 6, 6, 9];
    const expression = frameSeriesExpression(values);
    // Mirror ffmpeg's `if(lt(n,k),a,b)` evaluation in JavaScript and compare frame by frame.
    const evaluate = (n: number): number => {
      const parse = (text: string): number => {
        const match = /^if\(lt\(n,(\d+)\),(.*)\)$/.exec(text);
        if (!match) return Number(text);
        let depth = 0;
        const body = match[2]!;
        for (let index = 0; index < body.length; index += 1) {
          const character = body[index];
          if (character === "(") depth += 1;
          else if (character === ")") depth -= 1;
          else if (character === "," && depth === 0) {
            return n < Number(match[1]) ? parse(body.slice(0, index)) : parse(body.slice(index + 1));
          }
        }
        return Number(body);
      };
      return parse(expression);
    };
    values.forEach((value, index) => expect(evaluate(index)).toBe(value));
  });
});
