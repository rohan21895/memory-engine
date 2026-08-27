// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { CALIBRATION_MAX_THRESHOLD, CALIBRATION_MIN_PAIRS, CALIBRATION_MIN_THRESHOLD, calibrateMergeThreshold, calibrateThreshold, samePhotoImpostorScores } from "./face-calibration.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { SAME_PHOTO_DUPLICATE_SIMILARITY } from "./face-cluster.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-calibration self-check failed: ${message}`);
}

/** A unit vector whose cosine against [1, 0] is exactly `similarity`. */
const atCosine = (similarity: number) => [
  similarity,
  Math.sqrt(Math.max(0, 1 - similarity * similarity)),
];

/** `count` photos, each holding exactly two faces at a known cosine. */
function pairsAt(similarity: number, count: number, offset = 0) {
  const faces = [];
  for (let i = 0; i < count; i += 1) {
    const assetId = `asset-${offset + i}`;
    faces.push({ assetId, embedding: [1, 0] });
    faces.push({ assetId, embedding: atCosine(similarity) });
  }
  return faces;
}

const FALLBACK = 0.44;

// Thin evidence must not move the bar. A handful of pairs is a sample, not a
// distribution, and half-calibrating would ship a number nobody measured.
{
  const result = calibrateThreshold(pairsAt(0.1, CALIBRATION_MIN_PAIRS - 1), FALLBACK);
  assert(!result.calibrated, "must not calibrate below the minimum pair count");
  assert(
    result.threshold === FALLBACK,
    `must hold the fallback unchanged (got ${result.threshold})`,
  );
}

// With a real distribution the bar lands on the library's own impostor tail,
// NOT on the shipped constant -- this is the whole point of the module.
{
  const faces = [...pairsAt(0.1, 990), ...pairsAt(0.5, 10, 990)];
  const result = calibrateThreshold(faces, FALLBACK);
  assert(result.calibrated, "1000 pairs is enough to calibrate");
  assert(result.pairs === 1000, `expected 1000 pairs, got ${result.pairs}`);
  assert(
    Math.abs(result.threshold - 0.5) < 1e-6,
    `bar must sit on the 0.5% tail (got ${result.threshold})`,
  );
  assert(
    result.threshold !== FALLBACK,
    "a calibrated bar must be able to differ from the cold-start default",
  );
}

// One person can appear twice in a single frame -- a mirror, a collage, a
// photo-of-a-photo. Those pairs are genuine matches wearing an impostor label,
// and they land at the very top, exactly where the quantile is read. If they
// are not dropped they drag the bar up and the library under-merges forever.
{
  const clean = [...pairsAt(0.1, 990), ...pairsAt(0.5, 10, 990)];
  const withMirrors = [
    ...clean,
    ...pairsAt(SAME_PHOTO_DUPLICATE_SIMILARITY + 0.2, 40, 2000),
  ];
  const before = calibrateThreshold(clean, FALLBACK);
  const after = calibrateThreshold(withMirrors, FALLBACK);
  assert(
    after.pairs === before.pairs,
    `mirror pairs must be excluded (${before.pairs} -> ${after.pairs})`,
  );
  assert(
    Math.abs(after.threshold - before.threshold) < 1e-9,
    "mirror pairs must not move the bar",
  );
  assert(
    samePhotoImpostorScores(withMirrors).every(
      (score) => score < SAME_PHOTO_DUPLICATE_SIMILARITY,
    ),
    "no surviving pair may sit above the same-photo exception",
  );
}

// A degenerate library must not be able to produce an absurd bar in either
// direction: assignment stays strictly easier than merging, and never drops
// below the floor that held on real libraries.
{
  const tooHigh = calibrateThreshold(pairsAt(0.65, 400), FALLBACK);
  assert(
    tooHigh.threshold === CALIBRATION_MAX_THRESHOLD,
    `a heavy tail must clamp to the ceiling (got ${tooHigh.threshold})`,
  );
  const tooLow = calibrateThreshold(pairsAt(0.01, 400), FALLBACK);
  assert(
    tooLow.threshold === CALIBRATION_MIN_THRESHOLD,
    `an easy library must clamp to the floor (got ${tooLow.threshold})`,
  );
}

// Faces sitting alone in their photo carry no pair, so they contribute nothing.
{
  const singles = Array.from({ length: 500 }, (_, i) => ({
    assetId: `solo-${i}`,
    embedding: [1, 0],
  }));
  const result = calibrateThreshold(singles, FALLBACK);
  assert(result.pairs === 0, `single-face photos yield no pairs (got ${result.pairs})`);
  assert(!result.calibrated, "no pairs means no calibration");
}

// The merge bar reads the SAME negatives as a mean plus spread, not as a tail
// quantile, because a cluster-to-cluster average follows the different-person
// mean rather than its tail. It must therefore land well below the assignment
// bar on the same data -- that gap is the entire fix for one person showing up
// as two people.
{
  const faces = [...pairsAt(0.1, 990), ...pairsAt(0.5, 10, 990)];
  const assign = calibrateThreshold(faces, FALLBACK);
  const merge = calibrateMergeThreshold(faces, 0.6);
  assert(merge.calibrated, "1000 pairs is enough to calibrate a merge bar");
  assert(
    merge.threshold < 0.6,
    `the measured merge bar must relax the shipped constant (got ${merge.threshold})`,
  );
  assert(
    merge.threshold < assign.threshold,
    "averaging over many cross pairs is stronger evidence than one pair, so " +
      "the merge bar belongs BELOW the assignment bar, not above it",
  );
}

// It may only ever relax the bar it is given. A library whose negatives are so
// spread that the formula exceeds the fallback must not end up merging MORE
// easily than the strict constant would have.
{
  const wild = [...pairsAt(0.05, 300), ...pairsAt(0.65, 300, 300)];
  const merge = calibrateMergeThreshold(wild, 0.6);
  assert(
    merge.threshold <= 0.6,
    `must never exceed the fallback (got ${merge.threshold})`,
  );
}

// Thin evidence holds the strict constant, exactly like the assignment bar.
{
  const merge = calibrateMergeThreshold(
    pairsAt(0.1, CALIBRATION_MIN_PAIRS - 1),
    0.6,
  );
  assert(!merge.calibrated, "must not calibrate a merge bar below the minimum");
  assert(merge.threshold === 0.6, "must hold the strict constant unchanged");
}

// eslint-disable-next-line no-console
console.log("face-calibration self-check passed");
