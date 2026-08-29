// Times ONLY the consolidation sweep -- `extendFaceClusters(people, [], opts)`,
// which is exactly what `consolidatePeople` runs at the end of a small scan.
//
// Reports a same-process full-sweep control beside the touched-row path, so the
// speedup is not distorted by the much longer synthetic-library setup or load
// changes between two executions.
//
// Fast-path regression check only:
//   node --experimental-strip-types scratch/merge-sweep-bench/bench.ts --engagement-only
//
// Deterministic: a seeded LCG, so both checkouts see the same library.
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { clusterFaces, extendFaceClusters } from "../../apps/mobile/src/faces/face-cluster.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { MERGE_SIGMA, calibrateMergeThreshold } from "../../apps/mobile/src/faces/face-calibration.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import {
  FACE_INDEX_IDENTITY_MERGE_THRESHOLD,
  compareConsolidationBars,
  consolidationBarsFrom,
  faceClusterOptions,
  planConsolidationSweep,
} from "../../apps/mobile/src/faces/face-index.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import type { FaceObservation } from "../../apps/mobile/src/faces/types.ts";

const DIMS = 512;
const IDENTITIES = 2250;
const FACES = 17768;

let seed = 20260827;
function random(): number {
  seed = (seed * 1103515245 + 12345) % 2147483648;
  return seed / 2147483648;
}
function gaussian(): number {
  // Box-Muller. `random()` never returns 0 for this seed, but guard anyway.
  const u = Math.max(random(), 1e-12);
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * random());
}
function unit(values: number[]): number[] {
  let norm = 0;
  for (const value of values) norm += value * value;
  norm = Math.sqrt(norm) || 1;
  return values.map((value) => value / norm);
}

// A shared component across all identities, so different people sit at a
// realistic cosine rather than the ~0.04 of pure random 512-d vectors. Pure
// orthogonality would let the bound exit on the first block and flatter the
// result; this makes it work for the win.
const common = unit(Array.from({ length: DIMS }, gaussian));
const COMMON_WEIGHT = 0.45;

const centres = Array.from({ length: IDENTITIES }, () => {
  const own = Array.from({ length: DIMS }, gaussian);
  return unit(own.map((value, index) => value + COMMON_WEIGHT * common[index]));
});

const observations: FaceObservation[] = [];
for (let face = 0; face < FACES; face += 1) {
  const identity = face % IDENTITIES;
  const centre = centres[identity];
  observations.push({
    assetId: `asset-${face}`,
    embedding: unit(centre.map((value) => value + 0.12 * gaussian())),
    embeddingKind: "identity",
    capturedAt: 1_700_000_000_000 + face * 60_000,
  });
}

const options = { threshold: 0.449, constraints: [] };

// The timing population above deliberately has one face per asset, because
// same-photo cannot-links would change what that established benchmark times.
// This second population exists only for the engagement measurement. Its
// pair-score distribution is pinned to the real family-library fact that 4.1%
// of known-different pairs clear 0.20; generic random 512-d vectors hit the
// calibration's 0.30 floor and falsely make the moving bars look stable.
const CALIBRATION_PHOTOS = FACES / 2;
const calibrationObservations: FaceObservation[] = [];
for (let photo = 0; photo < CALIBRATION_PHOTOS; photo += 1) {
  // A normal approximation with this deterministic seed lands at the measured
  // family-library tail while retaining enough spread for the four/five-sigma
  // merge bars to be measurements rather than clamps.
  const score = Math.max(-0.25, Math.min(0.68, 0.075 + 0.07 * gaussian()));
  const assetId = `calibration-photo-${photo}`;
  calibrationObservations.push(
    { assetId, embedding: [1, 0], embeddingKind: "identity" },
    {
      assetId,
      embedding: [score, Math.sqrt(1 - score * score)],
      embeddingKind: "identity",
    },
  );
}

const barsFor = (faces: readonly FaceObservation[]) => {
  const evidenced = calibrateMergeThreshold(
    faces,
    FACE_INDEX_IDENTITY_MERGE_THRESHOLD,
  ).threshold;
  const temporal = calibrateMergeThreshold(
    faces,
    FACE_INDEX_IDENTITY_MERGE_THRESHOLD,
    { sigma: MERGE_SIGMA - 1 },
  ).threshold;
  return consolidationBarsFrom(
    faceClusterOptions(options.threshold, {
      evidencedMergeThreshold: evidenced,
      temporalMergeThreshold: temporal,
    }),
  );
};

// Eight successive 32-photo additions over the full-size synthetic library.
// This is the path-frequency measurement, separate from the timing below. A
// completed sweep persists the compare-time bars, so each iteration feeds its
// result into the next exactly as the index does across scans.
const FACES_PER_SMALL_SCAN = 32 * 2;
const ENGAGEMENT_RUNS = 8;
const settledFaceCount = FACES - FACES_PER_SMALL_SCAN * ENGAGEMENT_RUNS;
let readBars = barsFor(calibrationObservations.slice(0, settledFaceCount));
const executionProbe = clusterFaces(
  [
    { assetId: "probe-a", embedding: [1, 0], embeddingKind: "identity" },
    { assetId: "probe-b", embedding: [0, 1], embeddingKind: "identity" },
  ],
  { threshold: 0.99, skipMerge: true },
);
let fastTaken = 0;
let fastSkipped = 0;
let exactComparisonSkipped = 0;
for (let run = 1; run <= ENGAGEMENT_RUNS; run += 1) {
  const compareAt = settledFaceCount + run * FACES_PER_SMALL_SCAN;
  const compareBars = barsFor(calibrationObservations.slice(0, compareAt));
  const exactComparison = compareConsolidationBars(readBars, compareBars);
  if (!exactComparison.equal) exactComparisonSkipped += 1;
  const plan = planConsolidationSweep(readBars, compareBars);
  let executedPath: "full" | "restricted" | undefined;
  extendFaceClusters(executionProbe, [], {
    threshold: 0.99,
    mergeSeedPersonIds: plan.restricted
      ? new Set([executionProbe[0].id])
      : undefined,
    onMergeSweep: (path) => {
      executedPath = path;
    },
  });
  if (executedPath === "restricted") fastTaken += 1;
  else fastSkipped += 1;
  console.log(
    JSON.stringify({
      run,
      facesAtRead: compareAt - FACES_PER_SMALL_SCAN,
      facesAtCompare: compareAt,
      fast:
        executedPath === "restricted"
          ? "taken"
          : executedPath === "full"
            ? "skipped"
            : "not-run",
      planned: plan.restricted ? "restricted" : "full",
      read: readBars,
      compare: compareBars,
      delta: plan.delta,
      applied: plan.bars,
      exactComparison: exactComparison.equal ? "equal" : "moved",
    }),
  );
  readBars = plan.bars;
}
const calibrationScores = calibrationObservations.filter(
  (_face, position) => position % 2 === 1,
).map((face) => face.embedding[0]);
const abovePointTwo = calibrationScores.filter((score) => score > 0.2).length;
console.log(
  JSON.stringify({
    engagement: { fastTaken, fastSkipped, exactComparisonSkipped },
    calibration: {
      pairs: calibrationScores.length,
      abovePointTwo,
      abovePointTwoRate: abovePointTwo / calibrationScores.length,
    },
  }),
);

if (Math.abs(abovePointTwo / calibrationScores.length - 0.041) > 0.005) {
  throw new Error("merge engagement check invalid: family impostor tail drifted");
}
if (exactComparisonSkipped === 0) {
  throw new Error("merge engagement check invalid: moving-bar bug was not exercised");
}
if (fastTaken === 0 || fastSkipped !== 0) {
  throw new Error(
    `merge engagement regression: fast path taken=${fastTaken} skipped=${fastSkipped}`,
  );
}

if (process.argv.includes("--engagement-only")) process.exit(0);

const settleStart = Date.now();
const people = clusterFaces(observations, options);
const settleMs = Date.now() - settleStart;

// The measurement. Consolidation only -- no new faces are assigned inside the
// timed region, exactly the end-of-scan path after one small append changed a
// single existing person. `people` is already a fixed point at these same bars,
// which is the persisted-bar precondition for the restricted sweep.
const touched = new Set([people[0].id]);
const fullSweepStart = Date.now();
const fullSweep = extendFaceClusters(people, [], options);
const fullSweepMs = Date.now() - fullSweepStart;
const sweepStart = Date.now();
const consolidated = extendFaceClusters(people, [], {
  ...options,
  mergeSeedPersonIds: touched,
});
const sweepMs = Date.now() - sweepStart;

const faces = consolidated.reduce(
  (sum: number, person: { faceCount: number }) => sum + person.faceCount,
  0,
);
if (people.length === 0 || consolidated.length === 0 || faces !== observations.length) {
  throw new Error(
    `merge benchmark invalid: ${people.length} initial people, ${consolidated.length} after sweep, ${faces}/${observations.length} faces accounted for`,
  );
}
console.log(
  JSON.stringify({
    faces: observations.length,
    people: people.length,
    fullSweepAfter: fullSweep.length,
    afterSweep: consolidated.length,
    touched: touched.size,
    facesAccountedFor: faces,
    settleMs,
    fullSweepMs,
    sweepMs,
  }),
);
