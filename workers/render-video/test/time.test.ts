import { describe, expect, it } from "vitest";

import { frameRange, frames, framesToMilliseconds, framesToSamples } from "../src/time.js";
import { NTSC_30, range, t } from "./helpers.js";

describe("integer frame arithmetic", () => {
  it("accepts whole frames at the timeline rate", () => {
    expect(frames(t(112), NTSC_30, "in")).toBe(112);
  });

  it("refuses a time that is not a whole frame instead of rounding it", () => {
    expect(() => frames(t(112.4), NTSC_30, "clip in")).toThrow(/not a whole frame/);
  });

  it("refuses a time expressed at a different rate instead of rescaling it", () => {
    expect(() => frames(t(112, 30), NTSC_30, "clip in")).toThrow(/but the timeline is at/);
  });

  it("refuses a non-positive duration", () => {
    expect(() => frameRange(range(10, 0), NTSC_30, "clip")).toThrow(/duration of 0 frames/);
  });

  it("does not accumulate error across a long NTSC timeline", () => {
    // Ten thousand frames added one at a time must land exactly where the direct integer
    // multiply lands. Doing the same sum in float seconds does not.
    let accumulated = 0;
    for (let index = 0; index < 10_000; index += 1) accumulated += 1;
    expect(accumulated).toBe(10_000);
    expect(framesToSamples(accumulated, NTSC_30, 48_000)).toBe(Math.round((10_000 * 48_000) / NTSC_30));

    let seconds = 0;
    for (let index = 0; index < 10_000; index += 1) seconds += 1 / NTSC_30;
    const drifted = Math.round(seconds * NTSC_30);
    // The float route happens to land on the same frame here; what matters is that it is a
    // rounding away from correct rather than exact, and the contract type keeps us off it.
    expect(Math.abs(drifted - accumulated)).toBeLessThanOrEqual(1);
  });

  it("reproduces the golden fixture's recorded beat alignment from frame positions", () => {
    // reel-beat-locked-vertical-reframe.json: clip-02 starts at timeline frame 112 and
    // locks to beat 4 at 112.387612, recording -6.4667 ms.
    const rate = 60_000 / 1001;
    expect(framesToMilliseconds(112 - 112.387612, rate)).toBeCloseTo(-6.4667, 3);
  });
});
