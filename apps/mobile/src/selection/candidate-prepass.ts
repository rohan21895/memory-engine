import type { PickedPhoto } from "../import/picked-photo";
import type { MeasuredImageQuality } from "./image-quality";

/** Normal-sized picks keep the existing all-photo analysis path unchanged. */
export const CANDIDATE_PREPASS_THRESHOLD = 500;
/** Bound the native face/pose/semantic work for unusually large selections. */
export const HEAVY_ANALYSIS_CANDIDATE_LIMIT = 400;

export type ProbedCandidate = {
  photo: PickedPhoto;
  quality: MeasuredImageQuality;
};

type RankedCandidate = ProbedCandidate & {
  qualityScore: number;
  timeBucket?: number;
};

const MAX_TIME_BUCKETS = 40;

/**
 * Choose a quality-biased subset while explicitly rewarding underrepresented
 * time windows and places. The returned photos retain input order so the
 * downstream planner receives the same ordering contract as an uncapped build.
 */
export function chooseHeavyAnalysisCandidates(
  probed: readonly ProbedCandidate[],
  limit = HEAVY_ANALYSIS_CANDIDATE_LIMIT,
): ProbedCandidate[] {
  const normalizedLimit = Math.max(0, Math.floor(limit));
  if (normalizedLimit === 0 || probed.length === 0) return [];

  const unique = deduplicate(probed);
  if (unique.length <= normalizedLimit) return unique;

  const ranked = addTimeBuckets(unique, normalizedLimit);
  const selectedIds = new Set<string>();
  const timeCounts = new Map<number, number>();
  const placeCounts = new Map<string, number>();

  // User pins remain sovereign when a future edit flow feeds them into a
  // capped rebuild. The safety cap still wins if more than the limit are pinned.
  const pinned = ranked
    .filter(({ photo }) => photo.pinned)
    .sort(compareRankedCandidates)
    .slice(0, normalizedLimit);
  for (const candidate of pinned) {
    select(candidate, selectedIds, timeCounts, placeCounts);
  }

  const remaining = ranked.filter(({ photo }) => !selectedIds.has(photo.id));
  while (selectedIds.size < normalizedLimit && remaining.length > 0) {
    let bestIndex = 0;
    let bestPriority = candidatePriority(
      remaining[0],
      timeCounts,
      placeCounts,
    );

    for (let index = 1; index < remaining.length; index += 1) {
      const priority = candidatePriority(
        remaining[index],
        timeCounts,
        placeCounts,
      );
      if (
        priority > bestPriority ||
        (priority === bestPriority &&
          compareRankedCandidates(remaining[index], remaining[bestIndex]) < 0)
      ) {
        bestIndex = index;
        bestPriority = priority;
      }
    }

    const [winner] = remaining.splice(bestIndex, 1);
    select(winner, selectedIds, timeCounts, placeCounts);
  }

  return unique.filter(({ photo }) => selectedIds.has(photo.id));
}

function deduplicate(probed: readonly ProbedCandidate[]): ProbedCandidate[] {
  const seen = new Set<string>();
  return probed.filter(({ photo }) => {
    if (seen.has(photo.id)) return false;
    seen.add(photo.id);
    return true;
  });
}

function addTimeBuckets(
  candidates: readonly ProbedCandidate[],
  limit: number,
): RankedCandidate[] {
  const timed = candidates
    .filter(({ photo }) => validTimestamp(photo.creationTime))
    .slice()
    .sort((left, right) =>
      (left.photo.creationTime as number) -
        (right.photo.creationTime as number) ||
      left.photo.id.localeCompare(right.photo.id),
    );
  const bucketCount = Math.min(
    MAX_TIME_BUCKETS,
    limit,
    Math.max(1, Math.ceil(Math.sqrt(timed.length))),
  );
  const bucketById = new Map<string, number>();
  timed.forEach(({ photo }, index) => {
    bucketById.set(
      photo.id,
      Math.min(bucketCount - 1, Math.floor((index * bucketCount) / timed.length)),
    );
  });

  return candidates.map((candidate) => ({
    ...candidate,
    qualityScore: cheapQualityScore(candidate),
    timeBucket: bucketById.get(candidate.photo.id),
  }));
}

function cheapQualityScore({ photo, quality }: ProbedCandidate): number {
  const sharpness = unitOrNeutral(quality.sharpness);
  const exposure = unitOrNeutral(quality.exposure);
  const clipping = unitOrNeutral(quality.clippedFraction, 0);
  const exposureBalance = 1 - Math.min(1, Math.abs(exposure - 0.5) * 2);
  const pixels =
    typeof photo.width === "number" &&
    typeof photo.height === "number" &&
    photo.width > 0 &&
    photo.height > 0
      ? photo.width * photo.height
      : 0;
  const resolution = Math.min(1, Math.log2(Math.max(1, pixels)) / 24);
  return (
    sharpness * 0.55 +
    exposureBalance * 0.2 +
    (1 - clipping) * 0.15 +
    resolution * 0.1
  );
}

function candidatePriority(
  candidate: RankedCandidate,
  timeCounts: ReadonlyMap<number, number>,
  placeCounts: ReadonlyMap<string, number>,
): number {
  const timeCount =
    candidate.timeBucket === undefined
      ? undefined
      : timeCounts.get(candidate.timeBucket) ?? 0;
  const place = normalizedPlace(candidate.photo.placeKey);
  const placeCount = place ? placeCounts.get(place) ?? 0 : undefined;

  // A first representative for a time window outweighs up to one full point
  // of quality. This guarantees broad chronology before taking repeats.
  const timeCoverage =
    timeCount === undefined ? 0 : timeCount === 0 ? 1.1 : 0.16 / (timeCount + 1);
  const placeCoverage =
    placeCount === undefined
      ? 0
      : placeCount === 0
        ? 0.45
        : 0.1 / (placeCount + 1);
  return candidate.qualityScore + timeCoverage + placeCoverage;
}

function select(
  candidate: RankedCandidate,
  selectedIds: Set<string>,
  timeCounts: Map<number, number>,
  placeCounts: Map<string, number>,
): void {
  selectedIds.add(candidate.photo.id);
  if (candidate.timeBucket !== undefined) {
    timeCounts.set(
      candidate.timeBucket,
      (timeCounts.get(candidate.timeBucket) ?? 0) + 1,
    );
  }
  const place = normalizedPlace(candidate.photo.placeKey);
  if (place) placeCounts.set(place, (placeCounts.get(place) ?? 0) + 1);
}

function compareRankedCandidates(
  left: RankedCandidate,
  right: RankedCandidate,
): number {
  return (
    right.qualityScore - left.qualityScore ||
    left.photo.id.localeCompare(right.photo.id)
  );
}

function normalizedPlace(value: string | undefined): string | undefined {
  const normalized = value?.trim();
  return normalized ? normalized : undefined;
}

function validTimestamp(value: number | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function unitOrNeutral(value: number | undefined, neutral = 0.5): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.min(1, value))
    : neutral;
}
