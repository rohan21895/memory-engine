/**
 * Measure how much user-labelled evidence exists for same-photo blocks.
 *
 * The persisted index has already applied every confirmed merge, so reading its
 * final people cannot recover the pair the user answered. Rebuild the current
 * graph without answers, resolve each durable face/asset anchor against that
 * partition, then count the distinct co-occurring pairs those answers label.
 *
 * Output is aggregate-only: no asset ids, person ids, paths, or filenames.
 *
 *   node --experimental-strip-types scratch/cooccurrence-answers/measure.ts \
 *     --index /path/to/face-index.json \
 *     --observations /path/to/face-observations.jsonl [--stored]
 *
 * `--stored` resolves against the current on-device partition and is cheap.
 * Without it, the harness rebuilds the answer-free graph so an answer can be
 * classified against the split it originally repaired.
 */

import { readFileSync } from "node:fs";
import process from "node:process";

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import {
  DEFAULT_MERGE_THRESHOLD,
  DEFAULT_PERCEPTUAL_THRESHOLD,
  MERGE_EVIDENCE_MIN_FACES,
  TEMPORAL_MERGE_WINDOW_MS,
  clusterFacesByGraph,
} from "../../apps/mobile/src/faces/face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { MIN_ANCHOR_MARGIN, type FaceConstraint } from "../../apps/mobile/src/faces/face-constraints.ts";
import type { FaceObservation, Person } from "../../apps/mobile/src/faces/types";

type StoredConstraint = Omit<FaceConstraint, "aFace" | "bFace"> & {
  aFace?: string;
  bFace?: string;
};

type StoredIndex = {
  people: Array<Omit<Person, "centroid"> & { centroid: string }>;
  constraints?: StoredConstraint[];
  threshold: number;
  consolidationBars?: {
    identity: number;
    perceptual: number;
    evidenced: number;
    temporal: number;
  };
};

type StoredObservation = {
  assetId: string;
  embedding: string;
  embeddingKind: "identity" | "perceptual";
  seedable?: boolean;
  capturedAt?: number;
};

function argument(name: string): string {
  const at = process.argv.indexOf(`--${name}`);
  const value = at === -1 ? undefined : process.argv[at + 1];
  if (!value) {
    console.error(
      "usage: measure.ts --index <face-index.json> --observations <face-observations.jsonl>",
    );
    process.exit(1);
  }
  return value;
}

function decode(value: string): number[] {
  const bytes = Buffer.from(value, "base64");
  return Array.from(
    new Int8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength),
    (component) => component / 127,
  );
}

function loadObservations(path: string): FaceObservation[] {
  const observations: FaceObservation[] = [];
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (!line) continue;
    const stored = JSON.parse(line) as StoredObservation;
    observations.push({
      assetId: stored.assetId,
      embedding: decode(stored.embedding),
      embeddingKind: stored.embeddingKind,
      ...(stored.seedable === undefined ? {} : { seedable: stored.seedable }),
      ...(stored.capturedAt === undefined ? {} : { capturedAt: stored.capturedAt }),
    });
  }
  return observations;
}

function loadConstraint(stored: StoredConstraint): FaceConstraint {
  return {
    kind: stored.kind,
    a: stored.a,
    b: stored.b,
    ...(stored.aFace ? { aFace: decode(stored.aFace) } : {}),
    ...(stored.bFace ? { bFace: decode(stored.bFace) } : {}),
  };
}

function scaledSimilarity(a: readonly number[], b: readonly number[]): number {
  if (a.length === 0 || a.length !== b.length) return Number.NEGATIVE_INFINITY;
  let dot = 0;
  let aSquared = 0;
  let bSquared = 0;
  for (let axis = 0; axis < a.length; axis += 1) {
    if (!Number.isFinite(a[axis]) || !Number.isFinite(b[axis])) {
      return Number.NEGATIVE_INFINITY;
    }
    dot += a[axis] * b[axis];
    aSquared += a[axis] * a[axis];
    bSquared += b[axis] * b[axis];
  }
  return dot / (Math.max(1, Math.sqrt(aSquared)) * Math.max(1, Math.sqrt(bSquared)));
}

function ownerOf(
  people: readonly Person[],
  claimantsByAsset: ReadonlyMap<string, readonly number[]>,
  assetId: string,
  face: readonly number[] | undefined,
  assignmentBar: number,
): number {
  const claimants = claimantsByAsset.get(assetId) ?? [];
  if (claimants.length === 0) return -1;
  if (claimants.length === 1) return claimants[0];
  if (!face) return -1;
  let best = -1;
  let bestScore = Number.NEGATIVE_INFINITY;
  let runnerUp = Number.NEGATIVE_INFINITY;
  for (const claimant of claimants) {
    const score = scaledSimilarity(face, people[claimant].centroid);
    if (score > bestScore) {
      runnerUp = bestScore;
      bestScore = score;
      best = claimant;
    } else if (score > runnerUp) {
      runnerUp = score;
    }
  }
  const bar = people[best]?.embeddingKind === "perceptual"
    ? DEFAULT_PERCEPTUAL_THRESHOLD
    : assignmentBar;
  if (best === -1 || bestScore < bar) return -1;
  if (
    runnerUp > Number.NEGATIVE_INFINITY &&
    bestScore - runnerUp < MIN_ANCHOR_MARGIN
  ) {
    return -1;
  }
  return best;
}

function sharedAssets(a: Person, b: Person): number {
  const left = new Set(a.assetIds);
  let shared = 0;
  for (const assetId of b.assetIds) if (left.has(assetId)) shared += 1;
  return shared;
}

function narrow(person: Person): boolean {
  return (
    person.firstAt !== undefined &&
    person.lastAt !== undefined &&
    person.lastAt - person.firstAt <= TEMPORAL_MERGE_WINDOW_MS
  );
}

function gap(a: Person, b: Person): number | undefined {
  if (
    a.firstAt === undefined ||
    a.lastAt === undefined ||
    b.firstAt === undefined ||
    b.lastAt === undefined
  ) {
    return undefined;
  }
  if (a.lastAt >= b.firstAt && b.lastAt >= a.firstAt) return 0;
  return a.lastAt < b.firstAt ? b.firstAt - a.lastAt : a.firstAt - b.lastAt;
}

function pairBar(
  a: Person,
  b: Person,
  assignmentBar: number,
  bars: NonNullable<StoredIndex["consolidationBars"]>,
): number {
  if (a.embeddingKind === "perceptual") return bars.perceptual;
  const evidenced =
    a.faceCount >= MERGE_EVIDENCE_MIN_FACES &&
    b.faceCount >= MERGE_EVIDENCE_MIN_FACES;
  if (!evidenced) return Math.max(assignmentBar, bars.identity);
  const distance = gap(a, b);
  const near =
    distance !== undefined &&
    distance <= TEMPORAL_MERGE_WINDOW_MS &&
    narrow(a) &&
    narrow(b);
  return near ? Math.min(bars.evidenced, bars.temporal) : bars.evidenced;
}

function percent(part: number, whole: number): string {
  return whole === 0 ? "n/a" : `${((100 * part) / whole).toFixed(1)}%`;
}

const index = JSON.parse(readFileSync(argument("index"), "utf8")) as StoredIndex;
const observations = loadObservations(argument("observations"));
const constraints = (index.constraints ?? []).map(loadConstraint);
const bars = index.consolidationBars ?? {
  identity: DEFAULT_MERGE_THRESHOLD,
  perceptual: DEFAULT_PERCEPTUAL_THRESHOLD,
  evidenced: DEFAULT_MERGE_THRESHOLD,
  temporal: DEFAULT_MERGE_THRESHOLD,
};

const useStored = process.argv.includes("--stored");
const startedAt = Date.now();
const people: Person[] = useStored
  ? index.people.map((person) => ({ ...person, centroid: decode(person.centroid) }))
  : clusterFacesByGraph(observations, { threshold: index.threshold });
const claimantsByAsset = new Map<string, number[]>();
people.forEach((person, personIndex) => {
  for (const assetId of person.assetIds) {
    const claimants = claimantsByAsset.get(assetId);
    if (claimants) claimants.push(personIndex);
    else claimantsByAsset.set(assetId, [personIndex]);
  }
});

type Label = {
  answer: number;
  kind: FaceConstraint["kind"];
  pair: string;
  cooccurring: boolean;
  blocked: boolean;
  rate: number;
};

const labels: Label[] = [];
let unresolved = 0;
let alreadyTogether = 0;
for (let answer = 0; answer < constraints.length; answer += 1) {
  const constraint = constraints[answer];
  const a = ownerOf(
    people,
    claimantsByAsset,
    constraint.a,
    constraint.aFace,
    index.threshold,
  );
  const b = ownerOf(
    people,
    claimantsByAsset,
    constraint.b,
    constraint.bFace,
    index.threshold,
  );
  if (a === -1 || b === -1) {
    unresolved += 1;
    continue;
  }
  if (a === b) {
    alreadyTogether += 1;
    continue;
  }
  const first = Math.min(a, b);
  const second = Math.max(a, b);
  const left = people[first];
  const right = people[second];
  const shared = sharedAssets(left, right);
  const similarity = scaledSimilarity(left.centroid, right.centroid);
  labels.push({
    answer,
    kind: constraint.kind,
    pair: `${first}:${second}`,
    cooccurring: shared > 0,
    blocked: shared > 0 && similarity >= pairBar(left, right, index.threshold, bars),
    rate: shared / Math.max(1, Math.min(left.assetIds.length, right.assetIds.length)),
  });
}

const distinct = (rows: readonly Label[]): Map<string, Label[]> => {
  const grouped = new Map<string, Label[]>();
  for (const row of rows) {
    const known = grouped.get(row.pair);
    if (known) known.push(row);
    else grouped.set(row.pair, [row]);
  }
  return grouped;
};
const cooccurring = labels.filter((label) => label.cooccurring);
const blocked = labels.filter((label) => label.blocked);
const distinctCooccurring = distinct(cooccurring);
const distinctBlocked = distinct(blocked);
const contradictions = [...distinct(labels).values()].filter(
  (pair) => new Set(pair.map((label) => label.kind)).size > 1,
).length;
const rates = (kind: FaceConstraint["kind"]): number[] =>
  [...distinctBlocked.values()]
    .filter((pair) => pair.some((label) => label.kind === kind))
    .map((pair) => pair[0].rate)
    .sort((a, b) => a - b);
const rateRange = (values: readonly number[]): string =>
  values.length === 0
    ? "none"
    : `${percent(values[0], 1)}..${percent(values.at(-1) as number, 1)}`;

console.log(`${useStored ? "stored" : "reclustered"} people             ${people.length} (${Date.now() - startedAt}ms)`);
console.log(`persisted answers           ${constraints.length}`);
console.log(`  same / different          ${constraints.filter((c) => c.kind === "must").length} / ${constraints.filter((c) => c.kind === "cannot").length}`);
console.log(`  unresolved anchors        ${unresolved}`);
console.log(`  already one base cluster  ${alreadyTogether}`);
console.log(`  resolve to distinct pair  ${labels.length}`);
console.log(`co-occurring answer pairs   ${cooccurring.length} answers / ${distinctCooccurring.size} distinct pairs`);
console.log(`  labelled same / different ${cooccurring.filter((x) => x.kind === "must").length} / ${cooccurring.filter((x) => x.kind === "cannot").length}`);
console.log(`  among first 38 / later     ${cooccurring.filter((x) => x.answer < 38).length} / ${cooccurring.filter((x) => x.answer >= 38).length}`);
console.log(`  co-occurrence rate range   ${rateRange(cooccurring.map((x) => x.rate).sort((a, b) => a - b))}`);
console.log(`over-bar blocked pairs      ${blocked.length} answers / ${distinctBlocked.size} distinct pairs`);
console.log(`  labelled same / different ${blocked.filter((x) => x.kind === "must").length} / ${blocked.filter((x) => x.kind === "cannot").length}`);
console.log(`  same rate range           ${rateRange(rates("must"))}`);
console.log(`  different rate range      ${rateRange(rates("cannot"))}`);
console.log(`contradictory pair labels   ${contradictions}`);
