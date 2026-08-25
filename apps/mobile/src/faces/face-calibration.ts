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
import { DEFAULT_MERGE_THRESHOLD, SAME_PHOTO_EXCEPTION_SIMILARITY } from "./face-cluster.ts";

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
        if (score < SAME_PHOTO_EXCEPTION_SIMILARITY) {
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
