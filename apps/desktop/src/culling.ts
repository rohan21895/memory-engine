import type { LibraryItem } from "./types";

export type CullingSelectionSummary = {
  total: number;
  photos: number;
  videos: number;
  other: number;
  measuredVideoCount: number;
  unknownDurationVideos: number;
  measuredVideoDurationMs: number;
};

export type QualityCoverage = {
  total: number;
  comparable: number;
  differentlyMeasured: number;
  unmeasured: number;
};

const PHOTO_KINDS = new Set<LibraryItem["kind"]>([
  "image",
  "live_photo",
  "motion_photo",
]);

export function toggleCullingSelection(
  selected: ReadonlySet<string>,
  mediaId: string,
): Set<string> {
  const next = new Set(selected);
  if (next.has(mediaId)) next.delete(mediaId);
  else next.add(mediaId);
  return next;
}

export function summarizeCullingSelection(
  items: readonly LibraryItem[],
  selected: ReadonlySet<string>,
): CullingSelectionSummary {
  const summary: CullingSelectionSummary = {
    total: 0,
    photos: 0,
    videos: 0,
    other: 0,
    measuredVideoCount: 0,
    unknownDurationVideos: 0,
    measuredVideoDurationMs: 0,
  };

  for (const item of items) {
    if (!selected.has(item.mediaId)) continue;
    summary.total += 1;
    if (PHOTO_KINDS.has(item.kind)) {
      summary.photos += 1;
      continue;
    }
    if (item.kind !== "video") {
      summary.other += 1;
      continue;
    }
    summary.videos += 1;
    if (item.durationMs !== null && Number.isFinite(item.durationMs) && item.durationMs >= 0) {
      summary.measuredVideoCount += 1;
      summary.measuredVideoDurationMs += item.durationMs;
    } else {
      summary.unknownDurationVideos += 1;
    }
  }
  return summary;
}

export function qualityCoverage(items: readonly LibraryItem[]): QualityCoverage {
  const coverage: QualityCoverage = {
    total: items.length,
    comparable: 0,
    differentlyMeasured: 0,
    unmeasured: 0,
  };
  for (const item of items) {
    if (item.quality === null || !Number.isFinite(item.quality)) coverage.unmeasured += 1;
    else if (item.qualityIsComparable) coverage.comparable += 1;
    else coverage.differentlyMeasured += 1;
  }
  return coverage;
}
