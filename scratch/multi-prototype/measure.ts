/**
 * Does multi-prototype identity beat the single centroid on THIS library, in
 * merges gained per impostor admitted?
 *
 * The owner's library is 11,854 photos, 17,768 faces and 2,244 people for a
 * small family cast, and lowering the merge bar has already been ruled out as
 * the fix — every bar low enough to join the genuine splits admits more
 * different people than it gains merges. The remaining hypothesis is that the
 * REPRESENTATION is wrong: one weighted mean per person sits between a
 * three-month-old and a fourteen-month-old and matches neither, so the same
 * infant occupies several tiles that no threshold can reunite.
 *
 * WHAT IS MEASURED, and why it is a curve rather than a number. Two clusters
 * that appear in ONE photo are known-different-people; that is the same free
 * label `face-calibration.ts` calibrates the shipped bars from. So every pair of
 * clusters in the library falls into one of two counted populations:
 *
 *   IMPOSTORS  co-occurring pairs. Admitting one is a fusion — the failure the
 *              product cannot recover from. The clusterer's cannot-link stops
 *              these today, so this is counterfactual: it is the rate at which
 *              a rule fuses different people, measured on the only different
 *              people the library can name.
 *   MERGES     pairs that never co-occur and clear the bar. Presumed splits of
 *              one person. "Presumed" is load-bearing: nothing here proves they
 *              are the same person, which is exactly why the number is only
 *              ever read AGAINST an impostor count and never on its own.
 *
 * A statistic is better if and only if, at the SAME impostor count, it gains
 * more merges. That is a bar-free comparison, and it has to be, because
 * `prototypeLinkage` is a max over sub-centre pairs and a max sits above the
 * mean of the same draws for two different people just as reliably as for two
 * halves of one. Comparing the two statistics at one shared bar would measure
 * that offset and call it a result.
 *
 * THE ANSWER, on the owner's library: NO. Four findings, in the order they kill
 * the hypothesis.
 *
 *   1. THE TILES ARE NOT INCOHERENT. Of the 937 people holding two or more
 *      faces, ZERO have a mean intra-tile cosine below the library's calibrated
 *      assignment bar of 0.448 — the p05 is 0.477 and the median is 0.625. The
 *      eight largest tiles, 1,014 faces down to 260, sit between 0.583 and
 *      0.664, ABOVE the 0.512 bar at which this library says two clusters are
 *      the same person. The premise was that one mean sits between a
 *      three-month-old and a fourteen-month-old; on this library no tile spans
 *      anything like that, because the assignment bar never let one form. The
 *      infant's drift is BETWEEN tiles, not inside them, so there is no second
 *      appearance for a second prototype to isolate. Run at the library's own
 *      measured bar the rule splits 21 of 2,248 people.
 *
 *   2. THE GAIN IS THE MAX, NOT THE MODELLING. Cutting each person into the same
 *      k pieces of the same sizes AT RANDOM gains the same merges. At an
 *      impostor budget of 60 over well-evidenced pairs: 152 merges for the
 *      single centroid, 161 for the appearance split, 162 for the random
 *      control. At a budget of 80: 406, 436, 429. The control matches the real
 *      split, so what little the representation buys is the arithmetic fact that
 *      a max over k*k draws exceeds a mean — available to any partition, and to
 *      two DIFFERENT people just as much as to two halves of one.
 *
 *   3. WHERE THE SPLIT DOES BEAT ITS CONTROL, IT LOSES TO THE CENTROID. Splitting
 *      aggressively (bar 0.65, or always to the size cap) does pull ahead of its
 *      random control — 28 merges against 22 at a 40-impostor budget — but both
 *      sit BELOW the single centroid's 34. Buying more appearance modes buys
 *      impostors faster than merges.
 *
 *   4. NOTHING HELPS AT A SAFE RISK LEVEL. At impostor budgets of 0, 2, 4 and 8,
 *      every policy in the table — the shipped centroid included — gains exactly
 *      ZERO merges. There is no bar, under any representation measured here, at
 *      which a single split tile is reunited before known-different people start
 *      being fused. `MULTI_PROTOTYPE_ENABLED` therefore stays false.
 *
 * A FIFTH FINDING, unrelated to prototypes and worth more than they were. The
 * capture times this library carries are 73.3% exactly 0, 26.6% absent and 23
 * faces out of 17,768 real. `face-index.ts` guarded that field with
 * `Number.isFinite(asset.creationTime)`, and 0 is finite, so a MediaStore record
 * with no DATE_TAKEN was stored as the epoch. Every one of those people got
 * `firstAt = lastAt = 0`, `spanGap` returned 0 for every pair of them, and the
 * TEMPORAL merge bar — meant to be the relaxation for clusters close in time —
 * was applied to essentially every evidenced pair in the library.
 *
 * That is now fixed and MEASURED, with `--times` below. The answer is worse than
 * the bug. Three runs over these same faces:
 *
 *   --times stored, old guard   2,248 people   (the epoch, blanket discount)
 *   --times stored, guard fixed 2,258 people   (no usable time survives at all)
 *   --times spread              2,248 people   (perfect times over two years)
 *
 * The third run is byte-identical to the first: the same 2,248 tiles, the same
 * ten merges, the same faces in each. Recovering the capture times changes
 * NOTHING, because `spanGap` returns 0 for spans that merely OVERLAP and a
 * person photographed across a family library spans the whole library. With
 * genuine, well-separated times 70.9% of evidenced pairs still land inside the
 * 60-day window (92.1% over a six-month library). The window is not a window.
 *
 * The ten merges it performs are all large — 301+159, 214+46, 197+45, 176+43,
 * 134+20, 66+62, 104+15, 42+39, 33+13, 26+10 — and the price is legible in the
 * census this file now prints: relaxing the bar on every evidenced pair lets 25
 * MORE known-different-person pairs clear (26 -> 51) to gain 13 unlabelled ones.
 * Two thirds of everything that clears at the discount is demonstrably two
 * different people. The 0.6000 bar is 4.87 sigma on this library's own
 * different-person scale; 0.5124 is exactly 4.
 *
 * SO THE REPAIR IS NOT THE TIMES, IT IS THE RULE, and it shipped: `narrowSpan`
 * now requires BOTH clusters to be narrower than the window before the discount
 * applies, which is what "two moments in one timeline" always meant. With that
 * in place `--times spread` returns 2,258 — every one of the ten fusions refused
 * — while the adjacent-months case the mechanism was designed for still fires.
 *
 * ONE MORE THING `--times near` IS FOR. A recovery that returns the SAME instant
 * for every photo defeats `narrowSpan` completely: every span has width 0, so
 * every span is narrow, every gap is 0, and all ten merges come back (2,248).
 * That is not hypothetical — it is what MediaStore's DATE_MODIFIED looks like
 * after a library is copied between volumes, every mtime inside the same few
 * minutes. It is why `captureTime` in `face-index.ts` refuses to fall back to
 * DATE_MODIFIED even though it is free and always populated, and why the only
 * sound recovery left is EXIF DateTimeOriginal.
 *
 * WHERE THE MEMBERSHIP COMES FROM. Prototypes need each person's FACES, and
 * `face-index.json` stores only centroids. So the shipped clusterer is re-run
 * over the real observations and the membership is taken from its own
 * `onAssign`/`onMerge` callbacks. That reproduces the device's partition to
 * within its incremental batching and the user's 11 saved constraints (2,253
 * people against 2,244, identical 15,785 faces and 1,315 singletons), and it
 * has the property that matters more than exactness: the single-centroid
 * baseline and the prototypes are derived from the SAME membership, so nothing
 * separates the two curves except the representation.
 *
 * RAW SPACE. `USE_CENTERED_CLUSTERING` is false, so `centeredForClustering` is a
 * no-op, `embeddingMean` is never set and the stored centroids are raw.
 * Centering these faces would compare them against centroids from a different
 * space and silently invalidate every number below.
 *
 *   node --experimental-strip-types scratch/multi-prototype/measure.ts \
 *     --observations /path/to/face-observations.jsonl \
 *     [--index /path/to/face-index.json] \
 *     [--times stored|strict|near|spread[:days]] [--capturedat] [--dump file]
 *
 * The index is optional and is used only to check the re-clustered partition
 * against the device's. `--capturedat` stops after the capture-time work and
 * skips the prototype sweep; `--dump` writes one final person id per face, and
 * two dumps diff into the exact set of clusters one rule joins and another does
 * not. Run from apps/mobile so the relative imports resolve.
 */

import { readFileSync, writeFileSync } from "node:fs";
import process from "node:process";

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { extendFaceClusters, DEFAULT_MERGE_THRESHOLD, MERGE_EVIDENCE_MIN_FACES, TEMPORAL_MERGE_WINDOW_MS } from "../../apps/mobile/src/faces/face-cluster.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { calibrateMergeThreshold, calibrateThreshold, samePhotoImpostorScores, MERGE_SIGMA } from "../../apps/mobile/src/faces/face-calibration.ts";
// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { derivePrototypes, prototypeWeightFor, type Prototype, type PrototypeFace } from "../../apps/mobile/src/faces/face-prototypes.ts";
import type { FaceObservation, Person } from "../../apps/mobile/src/faces/types";

const DEFAULT_FACE_INDEX_THRESHOLD = 0.44;

type StoredObservation = {
  assetId: string;
  embedding: string;
  embeddingKind: "identity" | "perceptual";
  seedable?: boolean;
  capturedAt?: number;
};

function decodeEmbedding(value: string): number[] {
  const bytes = Buffer.from(value, "base64");
  const signed = new Int8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return Array.from(signed, (component) => component / 127);
}

/**
 * Unit-norms exactly as `extendFaceClusters` does on the way in.
 *
 * int8 dequantization leaves a vector a fraction off unit length, and every
 * identity in `face-prototypes.ts` — the coherence read off |c|, the claim that
 * a prototype dot product IS average linkage — holds only for unit members.
 */
function unit(values: number[]): number[] {
  let squared = 0;
  for (const value of values) squared += value * value;
  const length = Math.sqrt(squared);
  return length > Number.EPSILON ? values.map((value) => value / length) : values;
}

function argument(name: string): string | undefined {
  const at = process.argv.indexOf(`--${name}`);
  return at === -1 ? undefined : process.argv[at + 1];
}

function percent(part: number, whole: number): string {
  return whole === 0 ? "n/a" : `${((100 * part) / whole).toFixed(1)}%`;
}

const observationsPath = argument("observations");
if (!observationsPath) {
  console.error("usage: measure.ts --observations <face-observations.jsonl> [--index <face-index.json>] [--times stored|strict|near|spread[:days]] [--capturedat]");
  process.exit(1);
}

/**
 * What each face is allowed to claim as its capture time, so the temporal merge
 * window can be measured rather than assumed.
 *
 *   stored  exactly what the device wrote. On this library that is 73% zeros,
 *           and zero is a real instant to `spanGap`, so nearly every cluster
 *           overlaps nearly every other. This is TODAY.
 *   strict  the guard fix with nothing recovered: a non-positive time is no
 *           time. Every span the zeros were propping up disappears.
 *   near    every face shares one instant. Every span overlaps every other, so
 *           EVERY evidenced pair is nearInTime. This is the upper bound on what
 *           any recovery can do, and it is where `stored` already sits.
 *   spread  a SHAPE PROBE, not data. Assets are ordered by their MediaStore id
 *           and dealt evenly across `days`, giving every cluster a real span of
 *           realistic width. It answers one question only -- with genuine,
 *           well-separated times, how many evidenced pairs still land inside the
 *           60-day window? -- and no claim here rests on the specific values.
 *
 * `strict` is also the answer for "recovered times that are far apart": a gap
 * above the window and a missing span both make `nearInTime` false, so the two
 * are the same measurement.
 */
const timesMode = argument("times") ?? "stored";
const timesKind = timesMode.split(":")[0];
if (!["stored", "strict", "near", "spread"].includes(timesKind)) {
  console.error(`unknown --times mode ${timesMode}`);
  process.exit(1);
}
const SPREAD_DAYS = Number(timesMode.split(":")[1] ?? 730);
const NEAR_INSTANT = Date.UTC(2025, 0, 1);

// ---------------------------------------------------------------- load faces

const observations: FaceObservation[] = [];
let capturedAtZero = 0;
let capturedAtMissing = 0;
let capturedAtReal = 0;
const storedTimes: Array<number | undefined> = [];
for (const line of readFileSync(observationsPath, "utf8").split("\n")) {
  if (!line) continue;
  const stored = JSON.parse(line) as StoredObservation;
  if (stored.capturedAt === undefined) capturedAtMissing += 1;
  else if (stored.capturedAt === 0) capturedAtZero += 1;
  else capturedAtReal += 1;
  storedTimes.push(stored.capturedAt);
  observations.push({
    assetId: stored.assetId,
    embedding: unit(decodeEmbedding(stored.embedding)),
    embeddingKind: stored.embeddingKind,
    seedable: stored.seedable,
    ...(stored.capturedAt === undefined ? {} : { capturedAt: stored.capturedAt }),
  });
}

/**
 * Rewrites `capturedAt` under the chosen mode.
 *
 * Only the times move. The embeddings, the assignment order and every bar are
 * byte-identical across modes, so any difference in the outcome is the temporal
 * rule and nothing else.
 */
if (timesKind !== "stored") {
  const spreadAt = new Map<string, number>();
  if (timesKind === "spread") {
    const assetIds = [...new Set(observations.map((o) => o.assetId))].sort(
      (a, b) => Number(a) - Number(b) || a.localeCompare(b),
    );
    const step = (SPREAD_DAYS * 24 * 60 * 60 * 1000) / Math.max(1, assetIds.length - 1);
    assetIds.forEach((assetId, at) => spreadAt.set(assetId, NEAR_INSTANT + at * step));
  }
  observations.forEach((observation, at) => {
    const editable = observation as { capturedAt?: number };
    if (timesKind === "strict") {
      const kept = storedTimes[at];
      if (kept === undefined || kept > 0) return;
      delete editable.capturedAt;
      return;
    }
    editable.capturedAt =
      timesKind === "near" ? NEAR_INSTANT : (spreadAt.get(observation.assetId) as number);
  });
}
const usableTimes = observations.filter(
  (o) => typeof o.capturedAt === "number" && o.capturedAt > 0,
).length;
console.log(
  `capture times         --times ${timesMode}: ${usableTimes} of ${observations.length} faces carry a usable time`,
);

const assignmentBar = calibrateThreshold(observations, DEFAULT_FACE_INDEX_THRESHOLD);
const evidencedBar = calibrateMergeThreshold(observations, DEFAULT_MERGE_THRESHOLD);
const temporalBar = calibrateMergeThreshold(observations, DEFAULT_MERGE_THRESHOLD, {
  sigma: MERGE_SIGMA - 1,
});

console.log(`faces                 ${observations.length}`);
console.log(`photos                ${new Set(observations.map((o) => o.assetId)).size}`);
console.log(
  `assignment bar        ${assignmentBar.threshold.toFixed(4)} (${assignmentBar.pairs} same-photo impostor pairs)`,
);
console.log(`evidenced merge bar   ${evidencedBar.threshold.toFixed(4)}`);
console.log(`temporal merge bar    ${temporalBar.threshold.toFixed(4)}`);

/**
 * The library's own different-person scale, printed so every bar below can be
 * placed on it rather than asserted.
 *
 * `calibrateMergeThreshold` reads mean + sigma*sd of this same distribution and
 * then CLAMPS to the strict fallback, so the shipped evidenced bar of 0.600 is
 * a clamp rather than a measurement — the raw five-sigma value is above it and
 * is only visible here.
 */
const impostorScores = samePhotoImpostorScores(observations);
const impostorMean =
  impostorScores.reduce((sum: number, value: number) => sum + value, 0) / impostorScores.length;
const impostorSd = Math.sqrt(
  impostorScores.reduce((sum: number, value: number) => sum + (value - impostorMean) ** 2, 0) /
    impostorScores.length,
);
console.log(
  `same-photo impostors  mean ${impostorMean.toFixed(4)}  sd ${impostorSd.toFixed(4)}  ` +
    `3s ${(impostorMean + 3 * impostorSd).toFixed(4)}  4s ${(impostorMean + 4 * impostorSd).toFixed(4)}  ` +
    `5s ${(impostorMean + 5 * impostorSd).toFixed(4)}  6s ${(impostorMean + 6 * impostorSd).toFixed(4)}`,
);

// --------------------------------------------------------------- capture time

console.log("\nCAPTURE TIME (what the temporal merge window has to work with)");
console.log(`  absent            ${capturedAtMissing} faces`);
console.log(`  exactly 0         ${capturedAtZero} faces  ${percent(capturedAtZero, observations.length)}`);
console.log(`  a real timestamp  ${capturedAtReal} faces  ${percent(capturedAtReal, observations.length)}`);

// ------------------------------------------------------------- re-cluster

const membership = new Map<FaceObservation, string>();
const absorbedInto = new Map<string, string>();
const startedAt = Date.now();
const people = extendFaceClusters([], observations, {
  threshold: assignmentBar.threshold,
  identityMergeThreshold: DEFAULT_MERGE_THRESHOLD,
  evidencedMergeThreshold: evidencedBar.threshold,
  temporalMergeThreshold: temporalBar.threshold,
  onAssign: (observation: FaceObservation, personId: string) => {
    membership.set(observation, personId);
  },
  onMerge: (absorbedPersonId: string, survivingPersonId: string) => {
    absorbedInto.set(absorbedPersonId, survivingPersonId);
  },
}) as Person[];

/** Follows the absorb chain to the id a person ended the run under. */
function finalId(personId: string): string {
  let current = personId;
  for (let hops = 0; hops < absorbedInto.size + 1; hops += 1) {
    const next = absorbedInto.get(current);
    if (next === undefined) return current;
    current = next;
  }
  return current;
}

const facesByPerson = new Map<string, FaceObservation[]>();
for (const [observation, personId] of membership) {
  const id = finalId(personId);
  const known = facesByPerson.get(id);
  if (known) known.push(observation);
  else facesByPerson.set(id, [observation]);
}

console.log(`\nreclustered           ${people.length} people in ${Date.now() - startedAt}ms`);
{
  const sizes = people.map((person) => person.faceCount).sort((a, b) => b - a);
  console.log(
    `  faces placed        ${sizes.reduce((sum, size) => sum + size, 0)}   singletons ${sizes.filter((s) => s === 1).length}   >=${MERGE_EVIDENCE_MIN_FACES} faces ${sizes.filter((s) => s >= MERGE_EVIDENCE_MIN_FACES).length}`,
  );
  console.log(`  largest tiles       ${sizes.slice(0, 8).join(", ")}`);
  // Membership must reproduce the clusterer's own face counts exactly, or the
  // prototypes below are built from a different partition than the centroids
  // they are compared against and the whole comparison is void.
  let mismatched = 0;
  for (const person of people) {
    if ((facesByPerson.get(person.id)?.length ?? 0) !== person.faceCount) mismatched += 1;
  }
  console.log(`  membership mismatch ${mismatched} people (must be 0)`);
  if (mismatched > 0) process.exit(1);
}

const indexPath = argument("index");
if (indexPath) {
  const stored = JSON.parse(readFileSync(indexPath, "utf8")) as {
    people: Array<{ faceCount: number; firstAt?: number }>;
  };
  const sizes = stored.people.map((person) => person.faceCount).sort((a, b) => b - a);
  console.log(
    `  device index        ${stored.people.length} people, ${sizes.reduce((sum, size) => sum + size, 0)} faces, ${sizes.filter((s) => s === 1).length} singletons`,
  );
  const spanned = stored.people.filter((person) => person.firstAt !== undefined);
  console.log(
    `  device spans        ${spanned.length} people carry a capture span, ${spanned.filter((person) => person.firstAt === 0).length} of them at epoch 0`,
  );
}

// ---------------------------------------------------------- the pair universe

const DIMENSIONS = observations[0]?.embedding.length ?? 0;
const identityPeople = people.filter(
  (person) => person.embeddingKind === "identity" && person.centroid.length === DIMENSIONS,
);

/**
 * Flat vector bank with per-block trailing norms, so a pair can be abandoned as
 * soon as Cauchy-Schwarz says it cannot reach the floor.
 *
 * The sweep is 2.5M person pairs for the single-centroid curve and up to ten
 * million prototype pairs for the multi-prototype ones. Nearly every one of them
 * is two strangers, whose running dot product plus its own best case falls under
 * the floor within a block or two — the same trick `boundedSimilarity` uses in
 * the shipped assignment loop, reimplemented here over flat typed arrays because
 * this harness compares millions of pairs rather than thousands.
 */
const BLOCK = 64;
const BLOCKS = Math.ceil(DIMENSIONS / BLOCK);

type Bank = {
  data: Float64Array;
  suffix: Float64Array;
  inverse: Float64Array;
  count: number;
};

function makeBank(vectors: ReadonlyArray<readonly number[]>): Bank {
  const count = vectors.length;
  const data = new Float64Array(count * DIMENSIONS);
  const suffix = new Float64Array(count * (BLOCKS + 1));
  const inverse = new Float64Array(count);
  for (let index = 0; index < count; index += 1) {
    const vector = vectors[index];
    const base = index * DIMENSIONS;
    let squared = 0;
    for (let d = 0; d < DIMENSIONS; d += 1) {
      data[base + d] = vector[d];
      squared += vector[d] * vector[d];
    }
    inverse[index] = squared > 0 ? 1 / Math.max(1, Math.sqrt(squared)) : 0;
    const suffixBase = index * (BLOCKS + 1);
    let tail = 0;
    for (let block = BLOCKS - 1; block >= 0; block -= 1) {
      const start = block * BLOCK;
      const end = Math.min(start + BLOCK, DIMENSIONS);
      for (let d = start; d < end; d += 1) tail += data[base + d] * data[base + d];
      suffix[suffixBase + block] = Math.sqrt(tail);
    }
  }
  return { data, suffix, inverse, count };
}

function boundedScore(bank: Bank, a: number, b: number, required: number): number {
  const scale = bank.inverse[a] * bank.inverse[b];
  if (scale === 0) return Number.NEGATIVE_INFINITY;
  const requiredDot = required / scale;
  const aBase = a * DIMENSIONS;
  const bBase = b * DIMENSIONS;
  const aSuffix = a * (BLOCKS + 1);
  const bSuffix = b * (BLOCKS + 1);
  let dot = 0;
  for (let block = 0; block < BLOCKS; block += 1) {
    const start = block * BLOCK;
    const end = Math.min(start + BLOCK, DIMENSIONS);
    for (let d = start; d < end; d += 1) dot += bank.data[aBase + d] * bank.data[bBase + d];
    if (dot + bank.suffix[aSuffix + block + 1] * bank.suffix[bSuffix + block + 1] < requiredDot) {
      return Number.NEGATIVE_INFINITY;
    }
  }
  return dot * scale;
}

/**
 * Nothing below this can matter: `CALIBRATION_MIN_MERGE_THRESHOLD` is 0.30, so
 * no bar this codebase would consider sits under it, and a pair that cannot
 * reach it cannot appear in any row of the tables below.
 */
const FLOOR = 0.3;

const pairId = (a: number, b: number): number =>
  a < b ? a * identityPeople.length + b : b * identityPeople.length + a;

// Co-occurrence, the free impostor label: two clusters sharing a photo are two
// people.
const peopleByAsset = new Map<string, Set<number>>();
identityPeople.forEach((person, index) => {
  for (const assetId of person.assetIds) {
    const known = peopleByAsset.get(assetId);
    if (known) known.add(index);
    else peopleByAsset.set(assetId, new Set([index]));
  }
});
const coOccurring = new Set<number>();
for (const group of peopleByAsset.values()) {
  if (group.size < 2) continue;
  const members = [...group];
  for (let i = 0; i < members.length; i += 1) {
    for (let j = i + 1; j < members.length; j += 1) coOccurring.add(pairId(members[i], members[j]));
  }
}
const evidenced = new Set<number>();
identityPeople.forEach((person, index) => {
  if (person.faceCount >= MERGE_EVIDENCE_MIN_FACES) evidenced.add(index);
});
const bothEvidenced = (key: number): boolean =>
  evidenced.has(Math.floor(key / identityPeople.length)) &&
  evidenced.has(key % identityPeople.length);

console.log(`\nco-occurring cluster pairs (known different people): ${coOccurring.size}`);

// ------------------------------------- what the temporal window actually does

/**
 * The merge sweep's own rule, replayed over the finished partition.
 *
 * `extendFaceClusters` above already reports the headline -- how many people the
 * library ends with under this `--times` mode. What it cannot report is the
 * PRICE, because its cannot-link refuses every co-occurring pair before the bar
 * is ever consulted. So the same bar rule is re-run here with the cannot-link
 * lifted: a co-occurring pair that clears its bar is a fusion the constraint had
 * to catch, and is the only measurable estimate of how often the same bar fuses
 * two different people who never happen to share a photo -- where nothing
 * catches it.
 *
 * Both counts are read together and never apart. Merges alone are not evidence:
 * nothing here proves two clusters are one person.
 */
{
  const spans = identityPeople.map((person) => {
    let firstAt: number | undefined;
    let lastAt: number | undefined;
    for (const face of facesByPerson.get(person.id) ?? []) {
      const at = face.capturedAt;
      if (typeof at !== "number" || !Number.isFinite(at) || at <= 0) continue;
      firstAt = firstAt === undefined ? at : Math.min(firstAt, at);
      lastAt = lastAt === undefined ? at : Math.max(lastAt, at);
    }
    return { firstAt, lastAt };
  });
  const gapBetween = (i: number, j: number): number | undefined => {
    const a = spans[i];
    const b = spans[j];
    if (
      a.firstAt === undefined || a.lastAt === undefined ||
      b.firstAt === undefined || b.lastAt === undefined
    ) {
      return undefined;
    }
    if (a.lastAt >= b.firstAt && b.lastAt >= a.firstAt) return 0;
    return a.lastAt < b.firstAt ? b.firstAt - a.lastAt : a.firstAt - b.lastAt;
  };
  const relaxed = Math.min(evidencedBar.threshold, temporalBar.threshold);
  const bank = makeBank(identityPeople.map((person) => person.centroid));
  const tally = {
    spanned: spans.filter((span) => span.firstAt !== undefined).length,
    evidencedPairs: 0,
    nearPairs: 0,
    shipped: { impostors: 0, merges: 0 },
    alwaysStrict: { impostors: 0, merges: 0 },
    alwaysRelaxed: { impostors: 0, merges: 0 },
  };
  for (let i = 0; i < identityPeople.length; i += 1) {
    for (let j = i + 1; j < identityPeople.length; j += 1) {
      const evidencedPair = evidenced.has(i) && evidenced.has(j);
      const gap = evidencedPair ? gapBetween(i, j) : undefined;
      const nearInTime = gap !== undefined && gap <= TEMPORAL_MERGE_WINDOW_MS;
      if (evidencedPair) {
        tally.evidencedPairs += 1;
        if (nearInTime) tally.nearPairs += 1;
      }
      // Scoring only pairs that could reach the LOWEST bar in play keeps this
      // sweep bounded; anything below it clears none of the three rules.
      const lowest = Math.min(relaxed, evidencedBar.threshold, DEFAULT_MERGE_THRESHOLD);
      const score = boundedScore(bank, i, j, lowest);
      if (score < lowest) continue;
      const impostor = coOccurring.has(pairId(i, j));
      const record = (
        bucket: { impostors: number; merges: number },
        bar: number,
      ): void => {
        if (score < bar) return;
        if (impostor) bucket.impostors += 1;
        else bucket.merges += 1;
      };
      const strictBar = evidencedPair ? evidencedBar.threshold : DEFAULT_MERGE_THRESHOLD;
      record(tally.shipped, nearInTime ? relaxed : strictBar);
      record(tally.alwaysStrict, strictBar);
      record(tally.alwaysRelaxed, evidencedPair ? relaxed : DEFAULT_MERGE_THRESHOLD);
    }
  }
  console.log("\nTEMPORAL WINDOW ON THIS PARTITION");
  console.log(
    `  clusters with a span   ${tally.spanned} of ${identityPeople.length}`,
  );
  console.log(
    `  evidenced pairs        ${tally.evidencedPairs}, of which ${tally.nearPairs} (${percent(tally.nearPairs, tally.evidencedPairs)}) are nearInTime and get the ${relaxed.toFixed(4)} bar instead of ${evidencedBar.threshold.toFixed(4)}`,
  );
  console.log("  pairs clearing their bar (cannot-link lifted):");
  for (const [name, bucket] of [
    ["as shipped, under --times", tally.shipped],
    ["if no pair were ever near", tally.alwaysStrict],
    ["if every pair were near", tally.alwaysRelaxed],
  ] as const) {
    console.log(
      `    ${name.padEnd(28)} ${String(bucket.impostors).padStart(4)} impostors  ${String(bucket.merges).padStart(4)} merges`,
    );
  }
}

// One line per face, in file order: the id of the person it ended under. Two
// runs' dumps diff into the exact set of clusters one rule joins and the other
// does not, which is the only honest way to say "N people merge".
const dumpPath = argument("dump");
if (dumpPath) {
  writeFileSync(
    dumpPath,
    `${observations.map((observation) => finalId(membership.get(observation) ?? "?")).join("\n")}\n`,
  );
  console.log(`\nmembership dump       ${dumpPath}`);
}

if (process.argv.includes("--capturedat")) process.exit(0);

/**
 * Are the tiles internally incoherent at all? This is the premise being tested,
 * and it is testable directly rather than through the merge counts.
 *
 * A person's coherence is the mean cosine between distinct members, which for a
 * mean of unit vectors falls straight out of the centroid's own length — the
 * same identity `centroidScale` rests on. If a tile really does span a
 * three-month-old and a fourteen-month-old, that number is LOW and there is
 * something for a second prototype to find. If it is high, the tile is already
 * one appearance and subdividing it is subdividing a point.
 */
{
  const coherences: Array<{ id: string; faces: number; coherence: number }> = [];
  for (const person of identityPeople) {
    if (person.faceCount < 2) continue;
    let weight = 0;
    let squaredWeights = 0;
    for (const observation of facesByPerson.get(person.id) ?? []) {
      const contribution = prototypeWeightFor(observation.seedable);
      weight += contribution;
      squaredWeights += contribution * contribution;
    }
    let squared = 0;
    for (const value of person.centroid) squared += value * value;
    const spread = weight * weight - squaredWeights;
    if (spread <= Number.EPSILON) continue;
    coherences.push({
      id: person.id,
      faces: person.faceCount,
      coherence: (squared * weight * weight - squaredWeights) / spread,
    });
  }
  const sorted = [...coherences].sort((a, b) => a.coherence - b.coherence);
  const at = (q: number): string =>
    (sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))]?.coherence ?? 0).toFixed(3);
  console.log(`\nHOW COHERENT IS A TILE ALREADY? (${sorted.length} people with 2+ faces)`);
  console.log(
    `  mean intra-tile cosine   p05 ${at(0.05)}  p25 ${at(0.25)}  median ${at(0.5)}  p75 ${at(0.75)}  p95 ${at(0.95)}`,
  );
  console.log(
    `  below the assignment bar ${assignmentBar.threshold.toFixed(3)}: ${sorted.filter((entry) => entry.coherence < assignmentBar.threshold).length} people`,
  );
  console.log(
    `  below the merge bar ${temporalBar.threshold.toFixed(3)}: ${sorted.filter((entry) => entry.coherence < temporalBar.threshold).length} people`,
  );
  const biggest = [...coherences].sort((a, b) => b.faces - a.faces).slice(0, 8);
  console.log(
    `  the eight largest tiles: ${biggest.map((entry) => `${entry.faces}f@${entry.coherence.toFixed(3)}`).join("  ")}`,
  );
}

// ------------------------------------------------------------- the policies

type Scores = Map<number, number>;

function scorePrototypes(protoVectors: number[][], protoOwner: number[]): Scores {
  const bank = makeBank(protoVectors);
  const scores: Scores = new Map();
  for (let a = 0; a < bank.count; a += 1) {
    for (let b = a + 1; b < bank.count; b += 1) {
      if (protoOwner[a] === protoOwner[b]) continue;
      const score = boundedScore(bank, a, b, FLOOR);
      if (score < FLOOR) continue;
      const key = pairId(protoOwner[a], protoOwner[b]);
      const best = scores.get(key);
      if (best === undefined || score > best) scores.set(key, score);
    }
  }
  return scores;
}

const facesFor = (person: Person): PrototypeFace[] =>
  (facesByPerson.get(person.id) ?? []).map((observation) => ({
    embedding: observation.embedding,
    weight: prototypeWeightFor(observation.seedable),
  }));

type Policy = {
  name: string;
  /**
   * The coherence a sub-centre must reach before it stops being subdivided.
   * `-1` never splits (the shipped single centroid, reproduced through the same
   * code path). `1` always splits until the size cap or a degenerate half stops
   * it — the hypothesis's ceiling, because if the most aggressive representation
   * does not beat the centroid then no gentler choice of k can.
   */
  coherenceBar: number;
  /**
   * THE CONTROL, and the reason to believe any of the rows above it.
   *
   * A max over k*k sub-centre pairs is higher than the single mean no matter
   * what the sub-centres are — carve a cluster into k arbitrary pieces and the
   * best piece-to-piece pairing still beats the whole-to-whole one, purely
   * because a max over more draws is larger. So a policy that gains merges has
   * proved nothing until the SAME cluster, cut into the SAME number of pieces of
   * the SAME sizes at random, is shown to gain less.
   *
   * This row takes the k and the piece sizes the coherence rule chose and deals
   * the faces out at random instead of by appearance. If it matches the real
   * split, the split rule is decoration and the whole gain is max-inflation.
   */
  shuffle?: boolean;
};

/**
 * A sub-centre must be this self-similar before it stops being subdivided,
 * MEASURED rather than picked.
 *
 * This is the same average-linkage-between-two-groups-of-this-library's-faces
 * statistic the merge bars are read on, so the rule reads: keep subdividing a
 * person while its sub-centre agrees with itself LESS than the bar at which
 * this library would call two clusters the same person. A group that would not
 * merge with itself is not one appearance.
 *
 * The four-sigma reading is the temporal bar the app already ships and applies
 * to nearly every pair on this library (see the capture-time report above).
 */
const measuredCoherenceBar = temporalBar.threshold;

const policies: Policy[] = process.argv.includes("--sweep")
  ? [
      { name: "single centroid (k=1)", coherenceBar: -1 },
      ...[0.46, 0.5, 0.52, 0.54, 0.56, 0.58, 0.6, 0.65, 0.7, 1].map((bar) => ({
        name: `split under ${bar.toFixed(2)}`,
        coherenceBar: bar,
      })),
    ]
  : [
      { name: "single centroid (k=1)", coherenceBar: -1 },
      // Every split policy is followed by its OWN random control at the same k
      // and the same piece sizes. Reading a split row without the control
      // directly under it is how max-inflation gets mistaken for a result.
      ...[
        { label: `merge bar ${measuredCoherenceBar.toFixed(3)} (measured)`, bar: measuredCoherenceBar },
        { label: "0.55", bar: 0.55 },
        { label: "0.65", bar: 0.65 },
        { label: "the size cap, always", bar: 1 },
      ].flatMap(({ label, bar }) => [
        { name: `split under ${label}`, coherenceBar: bar },
        { name: `  ^ RANDOM control, same k/sizes`, coherenceBar: bar, shuffle: true },
      ]),
    ];

/** Deterministic PRNG, so the control row is reproducible run to run. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Weighted mean of unit members, the same quantity `buildPrototype` forms. */
function weightedMean(faces: readonly PrototypeFace[], members: readonly number[]): number[] {
  const centroid = new Array<number>(DIMENSIONS).fill(0);
  let weight = 0;
  for (const member of members) {
    const contribution = faces[member].weight ?? 1;
    weight += contribution;
    for (let d = 0; d < DIMENSIONS; d += 1) {
      centroid[d] += faces[member].embedding[d] * contribution;
    }
  }
  if (weight > 0) for (let d = 0; d < DIMENSIONS; d += 1) centroid[d] /= weight;
  return centroid;
}

type Result = {
  policy: Policy;
  scores: Scores;
  prototypes: number;
  multiPrototypePeople: number;
  kHistogram: Map<number, number>;
};

const results: Result[] = [];
for (const policy of policies) {
  const startedAtPolicy = Date.now();
  const protoVectors: number[][] = [];
  const protoOwner: number[] = [];
  const kHistogram = new Map<number, number>();
  let multiPrototypePeople = 0;
  identityPeople.forEach((person, index) => {
    const faces = facesFor(person);
    const prototypes = derivePrototypes(faces, {
      coherenceBar: policy.coherenceBar,
    }) as Prototype[];
    kHistogram.set(prototypes.length, (kHistogram.get(prototypes.length) ?? 0) + 1);
    if (prototypes.length > 1) multiPrototypePeople += 1;
    if (policy.shuffle && prototypes.length > 1) {
      const order = faces.map((_face, at) => at);
      const random = mulberry32(index + 1);
      for (let at = order.length - 1; at > 0; at -= 1) {
        const swap = Math.floor(random() * (at + 1));
        [order[at], order[swap]] = [order[swap], order[at]];
      }
      let taken = 0;
      for (const prototype of prototypes) {
        protoVectors.push(weightedMean(faces, order.slice(taken, taken + prototype.faceCount)));
        protoOwner.push(index);
        taken += prototype.faceCount;
      }
      return;
    }
    for (const prototype of prototypes) {
      protoVectors.push(prototype.centroid);
      protoOwner.push(index);
    }
  });
  const scores = scorePrototypes(protoVectors, protoOwner);
  results.push({
    policy,
    scores,
    prototypes: protoVectors.length,
    multiPrototypePeople,
    kHistogram,
  });
  const ks = [...kHistogram.entries()].sort((a, b) => a[0] - b[0]);
  console.log(
    `\n${policy.name}: ${protoVectors.length} prototypes, ${multiPrototypePeople} people split ` +
      `(${ks.map(([k, count]) => `k=${k}:${count}`).join("  ")}) in ${Date.now() - startedAtPolicy}ms`,
  );
}

const baseline = results[0];
{
  // The k=1 policy must reproduce the shipped merge sweep's own number exactly,
  // or every comparison below is against something other than today's rule.
  const centroidBank = makeBank(identityPeople.map((person) => person.centroid));
  let checked = 0;
  let worst = 0;
  for (let i = 0; i < identityPeople.length; i += 1) {
    for (let j = i + 1; j < identityPeople.length; j += 1) {
      const direct = boundedScore(centroidBank, i, j, FLOOR);
      if (direct < FLOOR) continue;
      checked += 1;
      worst = Math.max(worst, Math.abs(direct - (baseline.scores.get(pairId(i, j)) ?? 0)));
    }
  }
  console.log(
    `\nbaseline check: k=1 prototypes reproduce the stored centroids on ${checked} pairs, worst delta ${worst.toExponential(2)}`,
  );
}

// ------------------------------------------------------------- the tables

const bars = [0.6, 0.55, temporalBar.threshold, 0.5, 0.45, 0.42, 0.4, 0.38, 0.35, 0.32, 0.3];

function count(scores: Scores, bar: number, gate?: (key: number) => boolean): { impostors: number; merges: number } {
  let impostors = 0;
  let merges = 0;
  for (const [key, score] of scores) {
    if (score < bar) continue;
    if (gate && !gate(key)) continue;
    if (coOccurring.has(key)) impostors += 1;
    else merges += 1;
  }
  return { impostors, merges };
}

function fixedBarTable(title: string, gate?: (key: number) => boolean): void {
  console.log(`\n${title}`);
  console.log(
    `  bar     ${results.map((result, index) => (index === 0 ? "single".padStart(13) : `policy ${index}`.padStart(13))).join("")}`,
  );
  console.log(`          ${results.map(() => "imp/merge".padStart(13)).join("")}`);
  for (const bar of bars) {
    const cells = results.map((result) => {
      const { impostors, merges } = count(result.scores, bar, gate);
      return `${impostors}/${merges}`.padStart(13);
    });
    console.log(`  ${bar.toFixed(3)}   ${cells.join("")}`);
  }
  console.log(`  policies: ${policies.map((policy, index) => `${index}=${policy.name}`).join("; ")}`);
}

fixedBarTable("IMPOSTORS ADMITTED / MERGES GAINED — all cluster pairs");
fixedBarTable(
  `IMPOSTORS ADMITTED / MERGES GAINED — both clusters >= ${MERGE_EVIDENCE_MIN_FACES} faces`,
  bothEvidenced,
);

/**
 * The answer, read at EQUAL RISK rather than at an equal bar.
 *
 * `prototypeLinkage` is a max over sub-centre pairs, and a max sits above the
 * mean of the same draws for two DIFFERENT people just as reliably as for two
 * halves of one. Comparing the policies at one shared bar would measure that
 * offset and call it a result. So instead: for each impostor budget, run each
 * policy at the loosest bar that stays inside the budget, and report the merges
 * it buys. A policy wins only by standing above the baseline in this column.
 */
function atBudget(scores: Scores, budget: number, gate?: (key: number) => boolean): number {
  const sorted = [...scores.entries()]
    .filter(([key]) => !gate || gate(key))
    .sort((a, b) => b[1] - a[1]);
  let impostors = 0;
  let merges = 0;
  for (const [key] of sorted) {
    if (coOccurring.has(key)) {
      if (impostors + 1 > budget) break;
      impostors += 1;
    } else merges += 1;
  }
  return merges;
}

const budgets = [0, 2, 4, 8, 16, 40, 60, 80];

function budgetTable(title: string, gate?: (key: number) => boolean): void {
  console.log(`\n${title}`);
  console.log(`  merges gained at an impostor budget of:`);
  console.log(`  ${"policy".padEnd(38)}${budgets.map((b) => String(b).padStart(8)).join("")}`);
  for (const result of results) {
    console.log(
      `  ${result.policy.name.padEnd(38)}${budgets
        .map((budget) => String(atBudget(result.scores, budget, gate)).padStart(8))
        .join("")}`,
    );
  }
}

budgetTable("EQUAL-RISK COMPARISON — all cluster pairs");
budgetTable(
  `EQUAL-RISK COMPARISON — both clusters >= ${MERGE_EVIDENCE_MIN_FACES} faces`,
  bothEvidenced,
);

// ---------------------------------------------------- what the max actually did

console.log("\nWHAT THE PROTOTYPES CHANGED (against the single centroid)");
for (const result of results.slice(1)) {
  let lifted = 0;
  let liftTotal = 0;
  let biggest = 0;
  let liftedImpostors = 0;
  for (const [key, score] of result.scores) {
    const before = baseline.scores.get(key) ?? 0;
    if (score <= before + 1e-9) continue;
    lifted += 1;
    liftTotal += score - before;
    biggest = Math.max(biggest, score - before);
    if (coOccurring.has(key)) liftedImpostors += 1;
  }
  console.log(
    `  ${result.policy.name.padEnd(38)} raised ${String(lifted).padStart(5)} pairs ` +
      `(${liftedImpostors} of them known-different-people), mean +${(liftTotal / Math.max(1, lifted)).toFixed(4)}, max +${biggest.toFixed(4)}`,
  );
}

console.log(
  `\ntemporal merge window ${TEMPORAL_MERGE_WINDOW_MS / (24 * 60 * 60 * 1000)} days; ` +
    `capture times usable for it: ${capturedAtReal} of ${observations.length} faces`,
);
