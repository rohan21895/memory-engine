/**
 * Does holding face embeddings as int8 change who the app thinks is who?
 *
 * The saving is not in doubt — 89.5 MB of `number[]` against 15.5 MB of bytes on
 * the owner's 17,768-face library — but this is a library of somebody's family,
 * and a person wrongly FUSED is the one failure no later pass can repair. So the
 * question this answers is not "is it smaller" but "is it the SAME PARTITION".
 *
 * Four representations, one clusterer, one library. Each is fed to the shipped
 * `extendFaceClusters` exactly as `face-index.ts` feeds it, and each recalibrates
 * its own bars from its own same-photo impostor pairs, because that is what
 * `reclusterIfCalibrationChanged` does on the device:
 *
 *   baseline   `Array.from(int8, (c) => c / 127)` -- what ships today
 *   int8       the bytes, expanded through `dequantized`'s lookup table
 *   float32    `Math.fround(c / 127)` -- what a Float32Array store would give
 *   ulp        every component nudged by ONE unit in the last place
 *
 * The last two are not proposals, they are the control. `float32` is option 2
 * from EMBEDDING-MEMORY.md. `ulp` is the smallest perturbation a float64 can
 * carry, and it stands in for every scheme that is arithmetically equivalent but
 * not bit-identical -- an integer dot product scaled once at the end, a
 * reassociated product, anything that rounds differently in the last bit. If
 * `ulp` moves identity decisions then so can any of them, and the only safe
 * representation is one that is bit-identical rather than merely equal.
 *
 * WHAT IS COUNTED, and why it is pairs rather than tiles. Person ids are
 * renumbered by every run, so "1 more person" says nothing about who moved.
 * The comparison is therefore over the CO-CLUSTERING RELATION: for each pair of
 * faces, are they in the same tile or not. Against the baseline partition:
 *
 *   NEWLY JOINED  pairs the variant puts together that the baseline kept apart.
 *                 The dangerous direction.
 *   NEWLY SPLIT   pairs the variant separates that the baseline joined.
 *                 Repairable by the review queue.
 *
 * And the free impostor labels: two faces in ONE photo are known-different
 * people (minus the near-duplicate band, where one face was detected twice --
 * the same exclusion `face-calibration.ts` makes). Every such pair that lands in
 * one tile is a FUSION of two people the library itself can name, so it is
 * reported as an absolute count per variant, not only as a delta.
 *
 *   node --experimental-strip-types scratch/embedding-memory/int8-equivalence.ts \
 *     --observations /path/to/face-observations.jsonl
 */

import { readFileSync } from "node:fs";
import process from "node:process";

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { extendFaceClusters, dequantized, DEFAULT_MERGE_THRESHOLD, SAME_PHOTO_DUPLICATE_SIMILARITY } from "../../apps/mobile/src/faces/face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { calibrateMergeThreshold, calibrateThreshold, MERGE_SIGMA } from "../../apps/mobile/src/faces/face-calibration.ts";
import type { FaceEmbeddingVector, FaceObservation, Person } from "../../apps/mobile/src/faces/types";

const DEFAULT_FACE_INDEX_THRESHOLD = 0.44;

type StoredObservation = {
  assetId: string;
  embedding: string;
  embeddingKind: "identity" | "perceptual";
  seedable?: boolean;
  capturedAt?: number;
};

function argument(name: string): string | undefined {
  const at = process.argv.indexOf(`--${name}`);
  return at === -1 ? undefined : process.argv[at + 1];
}

const observationsPath = argument("observations");
if (!observationsPath) {
  console.error("usage: int8-equivalence.ts --observations <face-observations.jsonl>");
  process.exit(1);
}

// -------------------------------------------------------------- the four forms

/** The compact form, built exactly as `decodeEmbeddingBytes` builds it. */
function bytesOf(value: string): Int8Array {
  return new Int8Array(Buffer.from(value, "base64"));
}

const ULP_VIEW = new DataView(new ArrayBuffer(8));

/** The next representable double above `value`. Zero and NaN are left alone. */
function nextUp(value: number): number {
  if (value === 0 || !Number.isFinite(value)) return value;
  ULP_VIEW.setFloat64(0, value);
  const bits = ULP_VIEW.getBigUint64(0);
  ULP_VIEW.setBigUint64(0, value > 0 ? bits + 1n : bits - 1n);
  return ULP_VIEW.getFloat64(0);
}

type Form = { name: string; build: (bytes: Int8Array) => FaceEmbeddingVector };

const FORMS: Form[] = [
  {
    name: "baseline",
    build: (bytes) => Array.from(bytes, (component) => component / 127),
  },
  { name: "int8", build: (bytes) => bytes },
  {
    name: "float32",
    build: (bytes) => Array.from(bytes, (component) => Math.fround(component / 127)),
  },
  {
    name: "ulp",
    build: (bytes) => Array.from(bytes, (component) => nextUp(component / 127)),
  },
];

// ---------------------------------------------------------------- load faces

const stored: StoredObservation[] = [];
for (const line of readFileSync(observationsPath, "utf8").split("\n")) {
  if (line) stored.push(JSON.parse(line) as StoredObservation);
}
const allBytes = stored.map((observation) => bytesOf(observation.embedding));

// The harness rebuilds the compact form itself rather than importing
// `decodeEmbeddingBytes`, because `face-index.ts` pulls in expo and jpeg-js and
// will not load under bare node. So prove the two agree before trusting a word
// of what follows.
{
  const sample = allBytes[0];
  const reference = Array.from(
    new Int8Array(Buffer.from(stored[0].embedding, "base64")),
    (component) => component / 127,
  );
  const expanded = dequantized(sample);
  let mismatched = 0;
  for (let index = 0; index < reference.length; index += 1) {
    if (!Object.is(expanded[index], reference[index])) mismatched += 1;
  }
  if (mismatched > 0) {
    console.error(`decode disagreement on ${mismatched} components -- results void`);
    process.exit(1);
  }
}

function observationsIn(form: Form): FaceObservation[] {
  return stored.map((observation, index) => ({
    assetId: observation.assetId,
    embedding: form.build(allBytes[index]),
    embeddingKind: observation.embeddingKind,
    seedable: observation.seedable,
    ...(observation.capturedAt === undefined
      ? {}
      : { capturedAt: observation.capturedAt }),
  }));
}

console.log(`faces                 ${stored.length}`);
console.log(`photos                ${new Set(stored.map((o) => o.assetId)).size}`);

/**
 * Vacuity guard for the CONTROLS, and the most important one here.
 *
 * "float32 and ulp changed nothing" is only worth reading if float32 and ulp
 * actually changed something. Both are meant to be perturbations, and a build
 * function that quietly returned the baseline doubles would produce an identical
 * partition for the least interesting reason there is. So each form is measured
 * against the baseline BEFORE it is clustered: how many of the 9.1M components
 * differ at all, and by how much relative to the value.
 */
{
  console.log("\nperturbation against the baseline doubles, before clustering:");
  const reference = FORMS[0].build(allBytes[0]) as number[];
  for (const form of FORMS) {
    let differing = 0;
    let total = 0;
    let worst = 0;
    for (const bytes of allBytes) {
      const values = dequantized(form.build(bytes));
      const base = FORMS[0].build(bytes) as number[];
      for (let index = 0; index < values.length; index += 1) {
        total += 1;
        if (Object.is(values[index], base[index])) continue;
        differing += 1;
        const relative = Math.abs((values[index] - base[index]) / (base[index] || 1));
        if (relative > worst) worst = relative;
      }
    }
    console.log(
      `  ${form.name.padEnd(9)} ${String(differing).padStart(9)} of ${total} components differ` +
        `   worst relative delta ${worst.toExponential(2)}`,
    );
  }
  if (reference.length === 0) process.exit(1);
}

// -------------------------------------------------------------- one full run

type Run = {
  name: string;
  bars: { assignment: number; evidenced: number; temporal: number };
  people: number;
  /** Final tile index per face, in the input order. */
  tile: Int32Array;
  ms: number;
};

function cluster(form: Form): Run {
  const observations = observationsIn(form);
  const assignment = calibrateThreshold(observations, DEFAULT_FACE_INDEX_THRESHOLD);
  const evidenced = calibrateMergeThreshold(observations, DEFAULT_MERGE_THRESHOLD);
  const temporal = calibrateMergeThreshold(observations, DEFAULT_MERGE_THRESHOLD, {
    sigma: MERGE_SIGMA - 1,
  });

  const membership = new Map<FaceObservation, string>();
  const absorbedInto = new Map<string, string>();
  const startedAt = Date.now();
  const people = extendFaceClusters([], observations, {
    threshold: assignment.threshold,
    identityMergeThreshold: DEFAULT_MERGE_THRESHOLD,
    evidencedMergeThreshold: evidenced.threshold,
    temporalMergeThreshold: temporal.threshold,
    onAssign: (observation: FaceObservation, personId: string) => {
      membership.set(observation, personId);
    },
    onMerge: (absorbedPersonId: string, survivingPersonId: string) => {
      absorbedInto.set(absorbedPersonId, survivingPersonId);
    },
  }) as Person[];
  const ms = Date.now() - startedAt;

  const finalId = (personId: string): string => {
    let current = personId;
    for (let hops = 0; hops <= absorbedInto.size; hops += 1) {
      const next = absorbedInto.get(current);
      if (next === undefined) return current;
      current = next;
    }
    return current;
  };

  // Unassigned faces (`seedable: false` with no home) get their own negative
  // tile so they can never be counted as co-clustered with anything.
  const tile = new Int32Array(observations.length);
  const numbers = new Map<string, number>();
  for (let index = 0; index < observations.length; index += 1) {
    const personId = membership.get(observations[index]);
    if (personId === undefined) {
      tile[index] = -(index + 1);
      continue;
    }
    const id = finalId(personId);
    let number = numbers.get(id);
    if (number === undefined) {
      number = numbers.size;
      numbers.set(id, number);
    }
    tile[index] = number;
  }

  return {
    name: form.name,
    bars: {
      assignment: assignment.threshold,
      evidenced: evidenced.threshold,
      temporal: temporal.threshold,
    },
    people: people.length,
    tile,
    ms,
  };
}

// ----------------------------------------------------- co-clustering distance

function pairsWithin(counts: Iterable<number>): number {
  let total = 0;
  for (const count of counts) total += (count * (count - 1)) / 2;
  return total;
}

function sizes(tile: Int32Array): Map<number, number> {
  const counts = new Map<number, number>();
  for (const value of tile) counts.set(value, (counts.get(value) ?? 0) + 1);
  return counts;
}

/**
 * Pairs of faces the two partitions disagree about, via the contingency table.
 *
 * n^2 over 17,768 faces is 158M comparisons; the table gives the same two counts
 * from a single pass, because pairs together in BOTH partitions are exactly the
 * pairs inside one contingency cell.
 */
function disagreement(
  left: Int32Array,
  right: Int32Array,
): { newlyJoined: number; newlySplit: number } {
  const cells = new Map<string, number>();
  for (let index = 0; index < left.length; index += 1) {
    const key = `${left[index]}|${right[index]}`;
    cells.set(key, (cells.get(key) ?? 0) + 1);
  }
  const both = pairsWithin(cells.values());
  return {
    newlySplit: pairsWithin(sizes(left).values()) - both,
    newlyJoined: pairsWithin(sizes(right).values()) - both,
  };
}

// ------------------------------------------------------------ impostor labels

/**
 * Same-photo face pairs that are genuinely two people.
 *
 * The near-duplicate band is excluded at the same constant `face-calibration.ts`
 * and `dedupeFaceObservations` use, because a repeat detection of one head is a
 * genuine match wearing an impostor label.
 */
const impostorPairs: Array<[number, number]> = [];
{
  const byAsset = new Map<string, number[]>();
  for (let index = 0; index < stored.length; index += 1) {
    const group = byAsset.get(stored[index].assetId);
    if (group) group.push(index);
    else byAsset.set(stored[index].assetId, [index]);
  }
  const unit = (bytes: Int8Array): number[] => {
    const values = dequantized(bytes);
    let squared = 0;
    for (const value of values) squared += value * value;
    const length = Math.sqrt(squared);
    return length > Number.EPSILON ? values.map((value) => value / length) : values;
  };
  const units = new Map<number, number[]>();
  for (const group of byAsset.values()) {
    if (group.length < 2) continue;
    for (const index of group) units.set(index, unit(allBytes[index]));
    for (let i = 0; i < group.length; i += 1) {
      for (let j = i + 1; j < group.length; j += 1) {
        const a = units.get(group[i])!;
        const b = units.get(group[j])!;
        let dot = 0;
        for (let d = 0; d < a.length; d += 1) dot += a[d] * b[d];
        if (dot < SAME_PHOTO_DUPLICATE_SIMILARITY) impostorPairs.push([group[i], group[j]]);
      }
    }
    for (const index of group) units.delete(index);
  }
}
console.log(`same-photo impostor pairs  ${impostorPairs.length}`);

function fusedImpostors(tile: Int32Array): number {
  let fused = 0;
  for (const [a, b] of impostorPairs) if (tile[a] === tile[b]) fused += 1;
  return fused;
}

// ------------------------------------------------------------------- measure

// `--order baseline,int8,baseline,int8` reruns forms in a given sequence, which
// is the only way to read the `ms` column: a single pass of each measures heap
// growth and JIT warm-up as much as it measures the representation.
const order = argument("order")?.split(",") ?? FORMS.map((form) => form.name);
const runs = order.map((name) => {
  const form = FORMS.find((candidate) => candidate.name === name);
  if (!form) throw new Error(`unknown form ${name}`);
  return cluster(form);
});
const baseline = runs[0];

console.log("");
console.log(
  "form      bars (assign/evid/temporal)   people   fused impostors   " +
    "newly joined vs baseline   newly split   ms",
);
for (const run of runs) {
  const { newlyJoined, newlySplit } = disagreement(baseline.tile, run.tile);
  const identical =
    run === baseline
      ? ""
      : newlyJoined === 0 && newlySplit === 0 && run.people === baseline.people
        ? "   <- identical partition"
        : "";
  console.log(
    `${run.name.padEnd(9)} ` +
      `${run.bars.assignment.toFixed(6)} ${run.bars.evidenced.toFixed(4)} ${run.bars.temporal.toFixed(4)}   ` +
      `${String(run.people).padStart(6)}   ${String(fusedImpostors(run.tile)).padStart(15)}   ` +
      `${String(newlyJoined).padStart(24)}   ${String(newlySplit).padStart(11)}   ` +
      `${String(run.ms).padStart(6)}${identical}`,
  );
}

// Of the pairs a variant newly joins, how many are pairs the library itself can
// name as different people? This is the number the decision turns on.
console.log("");
for (const run of runs.slice(1)) {
  const gained = fusedImpostors(run.tile) - fusedImpostors(baseline.tile);
  console.log(
    `${run.name.padEnd(9)} impostor pairs fused that the baseline kept apart: ${gained}`,
  );
}

// Vacuity guard. `disagreement` returning 0/0 is the headline result for `int8`,
// so it has to be shown capable of returning anything else -- a comparison that
// cannot detect a difference proves nothing by failing to find one. Two probes:
// one face moved by hand, and the baseline against a deliberately coarser
// quantization that must disagree substantially.
{
  const moved = Int32Array.from(baseline.tile);
  const other = moved.findIndex((value) => value !== moved[0]);
  moved[0] = moved[other];
  const probe = disagreement(baseline.tile, moved);
  console.log(
    `\nvacuity: moving ONE face reports joined=${probe.newlyJoined} split=${probe.newlySplit} ` +
      `(both zero would mean the comparison is blind)`,
  );
  const coarse = cluster({
    name: "int4",
    build: (bytes) => Array.from(bytes, (component) => Math.round(component / 8) / 15.875),
  });
  const wrecked = disagreement(baseline.tile, coarse.tile);
  console.log(
    `vacuity: a 4-bit quantization reports ${coarse.people} people, ` +
      `joined=${wrecked.newlyJoined} split=${wrecked.newlySplit}, ` +
      `fused impostors ${fusedImpostors(coarse.tile)} ` +
      `(so the pipeline does react to a representation change)`,
  );
}
