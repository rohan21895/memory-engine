/**
 * Derives this library's assignment bar from this library's own faces.
 *
 * A shipped cosine constant cannot be right for everyone. The same model, the
 * same alignment and the same code measured across two real libraries put the
 * impostor p99 at 0.264 and 0.427 — the bar one library needs is 36% away from
 * the bar the other needs, and the previously shipped 0.20 admitted 5.3% of
 * different-person pairs in the first and 17.5% in the second. A constant tuned
 * on a benchmark (LFW put the same model's impostor p99 at 0.169) is tuned on
 * strangers, and reads as far safer than it is on a library of relatives, who
 * genuinely resemble each other.
 *
 * The way out is to stop shipping a number and ship a measurement instead. Two
 * faces in the SAME photo are near-certainly different people, so every library
 * carries its own labelled negatives for free — no annotation, no upload, and
 * no dependence on whose face it is. The bar is then set where THIS library's
 * impostors run out, which absorbs relatedness, demographics and camera quality
 * without ever naming them.
 */
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { DEFAULT_MERGE_THRESHOLD, SAME_PHOTO_DUPLICATE_SIMILARITY } from "./face-cluster.ts";

/** Faces this calibration reads. Structurally a subset of FaceObservation. */
export type CalibrationFace = {
  assetId: string;
  embedding: readonly number[];
};

/**
 * Fraction of different-person pairs the bar is allowed to admit.
 *
 * 0.5% rather than the conventional 1% because an assignment error here is
 * transitive: one bad link does not add one wrong face to an album, it fuses
 * two people permanently. Under-merging is recoverable by hand; a wrong merge
 * is the failure the product cannot survive.
 */
export const CALIBRATION_TARGET_FAR = 0.005;

/**
 * Below this many pairs the tail is noise, not a measurement.
 *
 * At n = 318 pairs the 99.5th percentile is only the second-highest value, so
 * it already moves under resampling; under ~200 a single mirror pair that slips
 * the exception below would set the bar on its own.
 */
export const CALIBRATION_MIN_PAIRS = 200;

/**
 * Hard bracket on anything the calibration may return.
 *
 * The floor is the point below which a bar stops being defensible on any
 * library measured so far. The ceiling stays under the merge bar because a bar
 * that meets it would make assignment stricter than merging and inverts the
 * whole policy — assignment must always be the easier of the two.
 */
export const CALIBRATION_MIN_THRESHOLD = 0.24;
export const CALIBRATION_MAX_THRESHOLD = DEFAULT_MERGE_THRESHOLD - 0.05;

export type CalibrationResult = {
  threshold: number;
  /** Same-photo pairs that survived the mirror filter. */
  pairs: number;
  /** False when the fallback was kept because there was not enough evidence. */
  calibrated: boolean;
};

/**
 * How many standard deviations above the different-person mean two CLUSTERS
 * must sit before they are considered the same person.
 *
 * Merging compares averages, not faces. Average linkage between two clusters is
 * the mean cosine over every cross pair, so for two clusters of different people
 * it concentrates near the impostor MEAN (0.091 measured on the maternity
 * library) rather than near the impostor tail (0.264 p99) that governs a single
 * assignment. Averaging over n*m pairs suppresses the tail instead of exposing
 * it, which is why a bar borrowed from single-pair statistics is far too strict
 * here: measured across 120 cluster pairs, the shipped 0.60 joined ZERO of them,
 * and the highest-scoring pair in the whole library was 0.466.
 *
 * Five sigma, not the conventional three or four, and the extra margin is the
 * whole point. On the maternity library four sigma lands near 0.34, which joins
 * roughly 16 of 120 cluster pairs -- well into the range (0.32-0.35) where
 * same-person and different-person pairs are no longer separable by eye or by
 * statistic. Five sigma lands near 0.41 and joins about six, all of them at
 * 3x the different-person median or better, including the 113-face and
 * 105-face halves of one person that sat at 0.442 and could never rejoin.
 *
 * The asymmetry is deliberate: a person left in two groups is one tap from
 * correct, while two people fused is unrecoverable, so the bar belongs above
 * the ambiguous band rather than inside it.
 */
export const MERGE_SIGMA = 5;

/** Never merge below this, whatever the statistics claim. */
export const CALIBRATION_MIN_MERGE_THRESHOLD = 0.3;

/**
 * The bar at which two well-evidenced clusters are the same person.
 *
 * Derived from the SAME same-photo negatives as the assignment bar, but read as
 * a mean plus spread rather than a tail quantile, because that is the statistic
 * a cluster-to-cluster average actually follows. Returns `fallback` unchanged
 * when the evidence is too thin, exactly like `calibrateThreshold`.
 */
export function calibrateMergeThreshold(
  faces: readonly CalibrationFace[],
  fallback: number,
  options?: { sigma?: number; minPairs?: number },
): CalibrationResult {
  const sigma = options?.sigma ?? MERGE_SIGMA;
  const minPairs = options?.minPairs ?? CALIBRATION_MIN_PAIRS;
  const scores = samePhotoImpostorScores(faces);
  if (scores.length < minPairs) {
    return { threshold: fallback, pairs: scores.length, calibrated: false };
  }
  const mean = scores.reduce((sum, value) => sum + value, 0) / scores.length;
  const variance =
    scores.reduce((sum, value) => sum + (value - mean) * (value - mean), 0) /
    scores.length;
  const raw = mean + sigma * Math.sqrt(variance);
  // Never LOOSER than the floor, and never stricter than the bar it replaces --
  // this may only relax merging, never tighten it past today's behaviour.
  const threshold = Math.min(
    fallback,
    Math.max(CALIBRATION_MIN_MERGE_THRESHOLD, raw),
  );
  return { threshold, pairs: scores.length, calibrated: true };
}

function cosine(a: readonly number[], b: readonly number[]): number {
  if (a.length === 0 || a.length !== b.length) {
    return 0;
  }
  let dot = 0;
  for (let i = 0; i < a.length; i += 1) {
    dot += a[i] * b[i];
  }
  return dot;
}

/**
 * Same-photo cosines, minus the pairs that are not actually two people.
 *
 * One person CAN appear twice in a single frame — a mirror, a collage, a
 * photo-of-a-photo — and the clusterer already has a name for that case. Those
 * pairs are genuine matches wearing an impostor label, and because they land at
 * the very top of the distribution they would drag the bar up hardest, exactly
 * where the quantile is read. Dropping them at the same constant the clusterer
 * uses keeps the two halves of the policy telling the same story.
 */
export function samePhotoImpostorScores(
  faces: readonly CalibrationFace[],
): number[] {
  const byAsset = new Map<string, CalibrationFace[]>();
  for (const face of faces) {
    if (!face.embedding || face.embedding.length === 0) {
      continue;
    }
    const group = byAsset.get(face.assetId);
    if (group) {
      group.push(face);
    } else {
      byAsset.set(face.assetId, [face]);
    }
  }
  const scores: number[] = [];
  for (const group of byAsset.values()) {
    for (let i = 0; i < group.length; i += 1) {
      for (let j = i + 1; j < group.length; j += 1) {
        const score = cosine(group[i].embedding, group[j].embedding);
        if (score < SAME_PHOTO_DUPLICATE_SIMILARITY) {
          scores.push(score);
        }
      }
    }
  }
  return scores;
}

/** Linear-interpolated quantile; `sorted` must be ascending. */
function quantile(sorted: readonly number[], q: number): number {
  if (sorted.length === 0) {
    return Number.NaN;
  }
  if (sorted.length === 1) {
    return sorted[0];
  }
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) {
    return sorted[lower];
  }
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
}

/**
 * The bar for this library, or `fallback` when the evidence is too thin.
 *
 * Returns the fallback UNCHANGED rather than a blend when under-evidenced: a
 * half-calibrated bar is a number nobody measured, and the cold-start default
 * is deliberately strict so that holding it is the safe direction to fail.
 */
export function calibrateThreshold(
  faces: readonly CalibrationFace[],
  fallback: number,
  options?: { targetFar?: number; minPairs?: number },
): CalibrationResult {
  const targetFar = options?.targetFar ?? CALIBRATION_TARGET_FAR;
  const minPairs = options?.minPairs ?? CALIBRATION_MIN_PAIRS;
  const scores = samePhotoImpostorScores(faces);
  if (scores.length < minPairs) {
    return { threshold: fallback, pairs: scores.length, calibrated: false };
  }
  const sorted = [...scores].sort((a, b) => a - b);
  const raw = quantile(sorted, 1 - targetFar);
  const threshold = Math.min(
    CALIBRATION_MAX_THRESHOLD,
    Math.max(CALIBRATION_MIN_THRESHOLD, raw),
  );
  return { threshold, pairs: scores.length, calibrated: true };
}
