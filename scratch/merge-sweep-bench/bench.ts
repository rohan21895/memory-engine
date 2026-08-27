// Times ONLY the consolidation sweep -- `extendFaceClusters(people, [], opts)`,
// which is exactly what `consolidatePeople` runs at the end of a small scan.
//
// Run identically in an unbounded and a bounded checkout; the only difference
// between the two runs is the code under test.
//
// Deterministic: a seeded LCG, so both checkouts see the same library.
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { clusterFaces, extendFaceClusters } from "../../apps/mobile/src/faces/face-cluster.ts";
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

const settleStart = Date.now();
const people = clusterFaces(observations, options);
const settleMs = Date.now() - settleStart;

// The measurement. Consolidation only -- no new faces, exactly the end-of-scan
// path for a library that just gained a handful of photos.
const sweepStart = Date.now();
const consolidated = extendFaceClusters(people, [], options);
const sweepMs = Date.now() - sweepStart;

const faces = consolidated.reduce(
  (sum: number, person: { faceCount: number }) => sum + person.faceCount,
  0,
);
console.log(
  JSON.stringify({
    faces: observations.length,
    people: people.length,
    afterSweep: consolidated.length,
    facesAccountedFor: faces,
    settleMs,
    sweepMs,
  }),
);
