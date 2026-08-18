import { describe, expect, it } from "vitest";

import { canSeekVideoInput } from "../src/filtergraph.js";
import type { ResolvedSource } from "../src/sources.js";

const NTSC_30 = 30_000 / 1_001;

function source(overrides: Partial<ResolvedSource> = {}): ResolvedSource {
  return {
    mediaRefId: "source",
    mediaId: "a".repeat(64),
    paths: ["source.mp4"],
    inputArgs: ["-r", "30", "-i", "source.mp4"],
    video: {
      codecName: "h264",
      startTimeSeconds: 0,
      width: 1920,
      height: 1080,
      frameRate: 30,
      frameCount: 1_800,
      pixelFormat: "yuv420p",
      colorTransfer: "bt709",
      rotation: 0,
    },
    audio: null,
    memberDigests: ["a".repeat(64)],
    frameCount: 1_800,
    formatName: "mov,mp4,m4a,3gp,3g2,mj2",
    ...overrides,
  };
}

describe("the conservative input-seek policy", () => {
  it("allows only the H.264/MP4 source and rates whose frame equivalence is exercised", () => {
    expect(canSeekVideoInput(source(), 30)).toBe(true);
    expect(canSeekVideoInput(source(), NTSC_30)).toBe(true);
    expect(canSeekVideoInput(source({ paths: ["source.MP4"] }), 30)).toBe(true);

    expect(canSeekVideoInput(source({ paths: ["one.mp4", "two.mp4"] }), 30)).toBe(false);
    expect(canSeekVideoInput(source({ formatName: "matroska,webm" }), 30)).toBe(false);
    // FFprobe reports the same mov/mp4/m4a/3gp/3g2/mj2 alias list for each of these
    // containers. The actual extension is the conservative distinction we proved.
    expect(canSeekVideoInput(source({ paths: ["source.mov"] }), 30)).toBe(false);
    expect(canSeekVideoInput(source({ paths: ["source.3gp"] }), 30)).toBe(false);
    expect(canSeekVideoInput(source({ video: { ...source().video!, codecName: "hevc" } }), 30)).toBe(false);
    expect(canSeekVideoInput(source({ video: { ...source().video!, startTimeSeconds: 0.5 } }), 30)).toBe(false);
    expect(canSeekVideoInput(source(), 25)).toBe(false);
  });
});
