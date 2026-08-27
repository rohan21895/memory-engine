/**
 * What would a quantized face model cost, measured on the owner's own library?
 *
 * Nothing in this repository could answer that, so no quantized model could be
 * adopted even where one converts cleanly -- and `w600k-mbf` converts to full
 * int8 without complaint. This runner is the missing half.
 *
 * Two things happen here.
 *
 * 1. THE HARNESS IS CHECKED AGAINST A KNOWN NUMBER. docs and
 *    apps/mobile/src/faces/face-constraints.ts record that 4.1% of
 *    different-person pairs in this library beat 0.20. If this runner
 *    reproduces that from the raw observations, its pair construction and its
 *    decode are right. If it does not, every other number it prints is
 *    suspect. A fidelity harness whose own calibration is unverified is worth
 *    less than no harness, because it produces confident numbers.
 *
 * 2. IT PRICES DRIFT. We cannot measure a quantized w600k directly -- that
 *    needs the model run over real face crops, and the crops live behind the
 *    phone. But we can measure the thing that actually decides whether a
 *    quantized model is safe: how many identity decisions flip per unit of
 *    embedding movement. That converts the abstract question "is int8 good
 *    enough?" into a number a future conversion can be held to: hit at least
 *    this mean cosine against fp32, or expect at least this many people to
 *    merge or split wrongly.
 *
 * WHY NOT LFW. Because this library is harder than LFW in the one direction
 * that matters. Relatives look alike; an infant at one month and at one year is
 * barely the same face. A model that clears LFW can still fuse a mother with
 * her daughter here, and LFW cannot warn anyone about it. The pairs below come
 * from the owner's photos.
 *
 * WHERE THE LABELS COME FROM, free and without anyone labelling anything:
 *   - IMPOSTORS: two faces detected in the SAME photo. Clustering already
 *     cannot-links them, so they are known to be different people.
 *   - GENUINE: two photos that contain exactly ONE face each and that the
 *     shipped index assigned to the same person. Restricting to single-face
 *     photos is what makes the assignment unambiguous -- in a group photo an
 *     asset id does not say WHICH face is the person's.
 *
 * Genuine labels are the shipped clustering's own opinion, not ground truth, so
 * the genuine column measures self-consistency under drift, and the impostor
 * column -- which needs no clustering at all -- is the trustworthy one.
 *
 * Run:
 *   node --experimental-strip-types scratch/quant-fidelity/measure.ts \
 *     <face-observations.jsonl> <face-index.json>
 *
 * NOTE ON SPACE: embeddings are RAW. USE_CENTERED_CLUSTERING is false, so
 * `centeredForClustering` is a no-op and `embeddingMean` is never set. Nothing
 * here centres anything.
 */

import { readFileSync } from "node:fs";
import process from "node:process";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { cosine, cosineAgreement, verificationShift } from "../../apps/mobile/src/quant/quant-fidelity.ts";

const [observationsPath, indexPath] = process.argv.slice(2);
if (!observationsPath || !indexPath) {
  console.error(
    "usage: measure.ts <face-observations.jsonl> <face-index.json>",
  );
  process.exit(2);
}

/** Stored as base64 int8, decoded as byte/127. face-index.ts:832-841. */
function decode(base64: string): number[] {
  const bytes = Buffer.from(base64, "base64");
  const out = new Array<number>(bytes.length);
  for (let index = 0; index < bytes.length; index += 1) {
    out[index] = bytes.readInt8(index) / 127;
  }
  return out;
}

type Observation = { assetId: string; embedding: number[] };

const observations: Observation[] = readFileSync(observationsPath, "utf8")
  .split("\n")
  .filter((line) => line.trim().length > 0)
  .map((line) => {
    const parsed = JSON.parse(line) as { assetId: string; embedding: string };
    return { assetId: parsed.assetId, embedding: decode(parsed.embedding) };
  });

const index = JSON.parse(readFileSync(indexPath, "utf8")) as {
  threshold: number;
  people: Array<{ id: string; assetIds: string[]; centroid: string }>;
};

console.log(
  `library: ${observations.length} faces, ${index.people.length} people, ` +
    `calibrated bar ${index.threshold.toFixed(4)}`,
);

// --- Pairs -----------------------------------------------------------------

const byAsset = new Map<string, number[]>();
observations.forEach((observation, position) => {
  const bucket = byAsset.get(observation.assetId);
  if (bucket) bucket.push(position);
  else byAsset.set(observation.assetId, [position]);
});

const impostor: Array<readonly [number, number]> = [];
for (const positions of byAsset.values()) {
  for (let i = 0; i < positions.length; i += 1) {
    for (let j = i + 1; j < positions.length; j += 1) {
      impostor.push([positions[i], positions[j]]);
    }
  }
}

const soloFace = new Map<string, number>();
for (const [assetId, positions] of byAsset) {
  if (positions.length === 1) soloFace.set(assetId, positions[0]);
}
const genuine: Array<readonly [number, number]> = [];
for (const person of index.people) {
  const positions = person.assetIds
    .map((assetId) => soloFace.get(assetId))
    .filter((position): position is number => position !== undefined);
  for (let i = 0; i < positions.length; i += 1) {
    for (let j = i + 1; j < positions.length; j += 1) {
      genuine.push([positions[i], positions[j]]);
    }
  }
}

console.log(
  `pairs: ${genuine.length} genuine (same person, single-face photos), ` +
    `${impostor.length} impostor (two faces in one photo)`,
);

const embeddings = observations.map((observation) => observation.embedding);
const rate = (pairs: Array<readonly [number, number]>, bar: number): number =>
  pairs.filter(([i, j]) => cosine(embeddings[i], embeddings[j]) >= bar).length /
  (pairs.length || 1);

// --- 1. Calibration check, and a correction to the record. -----------------
//
// The repository records "4.1% of different-person pairs beat 0.20" in
// face-constraints.ts and scratch/face-anchor-coverage, both as prose, with no
// construction attached. Reproducing it turned out to matter, because
// "different-person pair" has two readings on this data and they differ by 4x:
//
//   face vs ANOTHER PERSON'S CENTROID   3.95%   <- what 4.1% actually measured
//   face vs FACE in the same photo     16.79%
//
// The first is the assignment question -- "is this face one of that cluster's
// faces?" -- which is what anchor resolution asks, and what the surrounding
// prose in face-constraints.ts is about. A centroid is an average over many
// faces, so it is quieter than any single face, and pairs against it score
// lower. The second compares two raw observations and is much harsher.
//
// Both are correct; they answer different questions. The 4.1% is reproduced
// below as the CALIBRATION check, because matching a recorded number is what
// proves the int8 decode and the pairing are right. The gating set is the
// within-photo one, because it needs no clustering labels and because it is the
// hard case: two faces in one photo share illumination, sensor and moment, and
// in a family library they are frequently relatives.

const personIndexOf = new Map<number, number>();
index.people.forEach((person, personIndex) => {
  for (const assetId of person.assetIds) {
    const position = soloFace.get(assetId);
    if (position !== undefined) personIndexOf.set(position, personIndex);
  }
});
const centroids = index.people.map((person) => decode(person.centroid));

let calibrationSeed = 999;
const calibrationRandom = (): number => {
  calibrationSeed = (calibrationSeed * 1664525 + 1013904223) >>> 0;
  return calibrationSeed / 4294967296;
};
const labelled = [...personIndexOf.keys()];
let crossCentroidHits = 0;
const CROSS_SAMPLES = 200_000;
for (let drawn = 0; drawn < CROSS_SAMPLES; ) {
  const face = labelled[Math.floor(calibrationRandom() * labelled.length)];
  const person = Math.floor(calibrationRandom() * centroids.length);
  if (personIndexOf.get(face) === person) continue;
  if (cosine(embeddings[face], centroids[person]) >= 0.2) crossCentroidHits += 1;
  drawn += 1;
}
const crossCentroidRate = crossCentroidHits / CROSS_SAMPLES;

console.log(
  `\ncalibration check -- face vs another person's centroid, above 0.20: ` +
    `${(crossCentroidRate * 100).toFixed(2)}% (recorded: 4.1%)`,
);
if (Math.abs(crossCentroidRate - 0.041) > 0.015) {
  console.log(
    "  WARNING: does not match the record. Treat every number below as " +
      "unverified until the pair construction is reconciled.",
  );
} else {
  console.log(
    "  matches -- the int8 decode and the pair construction agree with the record.",
  );
}
console.log(
  `  for contrast, face vs face in the SAME photo above 0.20: ` +
    `${(rate(impostor, 0.2) * 100).toFixed(2)}% -- the harder reading, and the one gated below.`,
);

// --- 2. Where the bars sit today. ------------------------------------------

const BARS = [0.2, index.threshold, 0.6];
console.log("\nbaseline (stored int8 embeddings, the shipped state):");
console.log("  bar      genuine-accept   impostor-accept");
for (const bar of BARS) {
  console.log(
    `  ${bar.toFixed(4)}   ${(rate(genuine, bar) * 100).toFixed(2).padStart(8)}%   ` +
      `${(rate(impostor, bar) * 100).toFixed(2).padStart(8)}%`,
  );
}

// --- 3. The price of drift. ------------------------------------------------
//
// A quantized model does not move an embedding in a chosen direction; it moves
// it in an arbitrary one. Isotropic noise is therefore the right first model of
// it, and the sweep reports ACHIEVED mean cosine against the baseline rather
// than the noise scale, so a real conversion can be looked up by the only
// number it will ever report about itself.

function createRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}
function gaussian(random: () => number): number {
  const u = Math.max(random(), 1e-12);
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * random());
}

function perturb(scale: number, seed: number): number[][] {
  const random = createRandom(seed);
  return embeddings.map((row) => row.map((value) => value + gaussian(random) * scale));
}

/** Re-quantize to `bits`, the one drift we can apply exactly rather than model. */
function requantize(bits: number): number[][] {
  const levels = 2 ** (bits - 1) - 1;
  return embeddings.map((row) =>
    row.map((value) => Math.round(Math.max(-1, Math.min(1, value)) * levels) / levels),
  );
}

console.log(
  "\ndrift budget -- what a quantized face model costs, by how far it moves embeddings:",
);
console.log(
  "  mean-cos   flip@0.449   impostor-delta@0.449   flip@0.600   impostor-delta@0.600",
);

const candidates: Array<[string, number[][]]> = [
  ...[0.002, 0.005, 0.01, 0.02, 0.04, 0.08].map(
    (scale, position) => [`noise ${scale}`, perturb(scale, 1000 + position)] as [string, number[][]],
  ),
  ...[7, 6, 5, 4].map((bits) => [`${bits}-bit`, requantize(bits)] as [string, number[][]]),
];

for (const [label, candidate] of candidates) {
  const agreement = cosineAgreement(embeddings, candidate);
  const tight = verificationShift(embeddings, candidate, genuine, impostor, index.threshold);
  const loose = verificationShift(embeddings, candidate, genuine, impostor, 0.6);
  console.log(
    `  ${agreement.mean.toFixed(5)}   ${(tight.flipRate * 100).toFixed(3).padStart(8)}%   ` +
      `${(tight.impostorAcceptDelta * 100).toFixed(3).padStart(18)}%   ` +
      `${(loose.flipRate * 100).toFixed(3).padStart(8)}%   ` +
      `${(loose.impostorAcceptDelta * 100).toFixed(3).padStart(18)}%   ${label}`,
  );
}

console.log(
  "\nRead this as: a quantized w600k reporting mean cosine X against fp32 should be\n" +
    "expected to flip roughly the tabulated share of identity decisions. At the\n" +
    "calibrated bar, mean cosine 0.999 costs ~0.3% of decisions, 0.994 costs ~0.8%,\n" +
    "and 0.975 costs ~3.3%. A candidate that cannot hold ~0.999 is not a drop-in.",
);
console.log(
  "\nLIMIT OF THIS MODEL, stated because the table looks reassuring: every\n" +
    "impostor-delta above is NEGATIVE. Independent noise on two embeddings can only\n" +
    "push their cosine down, so this sweep produces wrong SPLITS and never a wrong\n" +
    "MERGE. Real quantization error is not independent -- it is a systematic\n" +
    "distortion shared by every embedding, and a shared component pulls faces\n" +
    "TOGETHER, which is the direction that fuses a mother with her daughter and that\n" +
    "no later pass repairs. So treat the flip-rate column as calibrated and the\n" +
    "impostor-delta column as a floor, not a forecast. Measuring the real direction\n" +
    "needs the actual quantized model over the actual crops; the harness is ready\n" +
    "for it, and `verificationShift` is where those two embedding sets go.",
);
