import { describe, expect, it } from "vitest";

import { qualityCoverage, summarizeCullingSelection, toggleCullingSelection } from "./culling";
import type { LibraryItem } from "./types";

function item(
  mediaId: string,
  kind: LibraryItem["kind"],
  durationMs: number | null,
  quality: number | null = 0.9,
  qualityIsComparable = true,
): LibraryItem {
  return {
    mediaId,
    kind,
    thumbnailProxyId: null,
    width: 1,
    height: 1,
    capturedUnix: null,
    capturePrecision: "unknown",
    quality,
    qualityIsComparable,
    favorite: false,
    rejected: false,
    sensitive: false,
    safetyUnknown: false,
    personIds: [],
    spanId: null,
    durationMs,
  };
}

describe("culling selection", () => {
  it("toggles an item without mutating the current selection", () => {
    const current = new Set(["photo"]);
    const added = toggleCullingSelection(current, "video");
    const removed = toggleCullingSelection(added, "photo");

    expect([...current]).toEqual(["photo"]);
    expect([...added]).toEqual(["photo", "video"]);
    expect([...removed]).toEqual(["video"]);
  });

  it("counts selected photos and videos while separating unknown video length", () => {
    const items = [
      item("still", "image", null),
      item("live", "live_photo", null),
      item("measured", "video", 65_100),
      item("measured-too", "video", 4_900),
      item("unknown", "video", null),
      item("not-selected", "video", 9_000),
    ];
    const summary = summarizeCullingSelection(
      items,
      new Set(["still", "live", "measured", "measured-too", "unknown"]),
    );

    expect(summary).toEqual({
      total: 5,
      photos: 2,
      videos: 3,
      other: 0,
      measuredVideoCount: 2,
      unknownDurationVideos: 1,
      measuredVideoDurationMs: 70_000,
    });
  });

  it("never folds absent or invalid video lengths into the measured total", () => {
    const summary = summarizeCullingSelection(
      [
        item("absent", "video", null),
        item("negative", "video", -1),
        item("invalid", "video", Number.NaN),
        item("zero", "video", 0),
      ],
      new Set(["absent", "negative", "invalid", "zero"]),
    );

    expect(summary.videos).toBe(4);
    expect(summary.measuredVideoCount).toBe(1);
    expect(summary.unknownDurationVideos).toBe(3);
    expect(summary.measuredVideoDurationMs).toBe(0);
  });
});

describe("quality coverage", () => {
  it("does not call differently measured or absent scores comparable", () => {
    const coverage = qualityCoverage([
      item("comparable", "image", null, 0.92, true),
      item("different", "image", null, 0.81, false),
      item("pending", "image", null, null, false),
    ]);

    expect(coverage).toEqual({
      total: 3,
      comparable: 1,
      differentlyMeasured: 1,
      unmeasured: 1,
    });
  });
});
