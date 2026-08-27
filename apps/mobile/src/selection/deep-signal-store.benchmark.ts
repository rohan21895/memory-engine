/**
 * What the Tier-B store costs, and what it can actually save.
 *
 *   node --experimental-strip-types src/selection/deep-signal-store.benchmark.ts
 *
 * Three questions, in the order that decides whether M2 is worth shipping:
 *
 *   1. What does a HIT cost? It replaces a stage measured on the owner's phone
 *      at 2,330 ms per photograph, so the answer needs to be small by a lot.
 *   2. What does a MISS add? Encoding and writing a record is pure overhead on
 *      the path that was already slow.
 *   3. How often does a hit happen? This is the honest one, and the answer is
 *      not "always": the candidate cap re-ranks the whole picked set, so a
 *      different filter can select a largely different 64 out of the same
 *      library. Measured here against the real `chooseHeavyAnalysisCandidates`.
 *
 * SCOPE, stated because the last benchmark in this directory had to state it
 * too: this is Node, on a laptop. No native image load, no TFLite, no
 * filesystem. It measures the JS the store adds and removes; it cannot measure
 * the 2,330 ms it removes, which is why that number is quoted from
 * `docs/DEEP-ANALYSIS-TIMING.md` rather than reproduced.
 */

import type { PickedPhoto } from "../import/picked-photo";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { chooseHeavyAnalysisCandidates, HEAVY_ANALYSIS_CANDIDATE_LIMIT, type ProbedCandidate } from "./candidate-prepass.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { qualityFromBlurhash } from "./candidate-quality-probe.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { decodeDeepSignalRecord, encodeDeepSignalRecord, parseDeepSignalShard, serializeDeepSignalShard, utf8ByteLength } from "./deep-signal-store.ts";
import type { DeepSignalRecord } from "./deep-signal-store";

/** Measured on the owner's phone, 3,000-photo build. */
const DEVICE_DEEP_MS_PER_PHOTO = 2_330;
const DEVICE_STAGES_MS = { cacheLoad: 21, candidateProbeWarm: 3_263, candidateRank: 4_046 };

const LIBRARY = 3_000;
const SHARD_RECORDS = 544; // a full 2 MB month
const ROUNDS = 9;
const BASE83 =
  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~";

function now(): number {
  return performance.now();
}

function median(values: number[]): number {
  const sorted = values.slice().sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)];
}

function round(value: number): number {
  return Math.round(value * 1_000) / 1_000;
}

/** A distinct but decodable 4x3 blurhash, so the content axis has real variety. */
function blurhashFor(seed: number): string {
  let state = (seed * 2_654_435_761) >>> 0;
  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
    return (value ^ (value >>> 14)) >>> 0;
  };
  // Size flag for 4x3 is fixed; everything after it is free to vary.
  const head = "L";
  let tail = "";
  for (let index = 0; index < 27; index += 1) tail += BASE83[next() % BASE83.length];
  return head + tail;
}

function vector(length: number, seed: number): number[] {
  return Array.from({ length }, (_, index) => Math.sin(seed * 3.1 + index * 0.37) * 0.4);
}

function recordFor(seed: number): DeepSignalRecord {
  return {
    analysisWidth: 1_280,
    analysisHeight: 960,
    perceptual: { embedding: vector(76, seed), faces: seed % 4 },
    boxes: Array.from({ length: seed % 3 }, () => ({
      x: 100.5, y: 220.25, width: 180, height: 190,
      leftEyeOpen: 0.93, rightEyeOpen: 0.88, smiling: 0.42,
    })),
    quality: {
      sharpness: 0.61, exposure: 0.48, clippedFraction: 0.012,
      faceSharpness: 0.71, subjectSharpness: 0.66, subjectBackgroundRatio: 0.58,
      blurhash: blurhashFor(seed),
    },
    pose: {
      keypoints: Array.from({ length: 17 }, (_, i) => [i / 17, 1 - i / 17] as [number, number]),
      scores: vector(17, seed + 1).map(Math.abs),
    },
    semantic: {
      embedding: vector(512, seed + 2),
      aesthetic: 0.51, composed: 0.44, cleanFrame: 0.61, sleeping: 0.02,
      awake: 0.98, embraceContext: 0.31, screenshotDocument: 0.004,
    },
  };
}

// --- 1 and 2. What a hit costs, and what a miss adds -----------------------

const shardEntries = new Map(
  Array.from({ length: SHARD_RECORDS }, (_, index) => [
    `key-${index}`,
    encodeDeepSignalRecord(recordFor(index)),
  ]),
);
const shardText = serializeDeepSignalShard(shardEntries);
const wantedKeys = Array.from({ length: HEAVY_ANALYSIS_CANDIDATE_LIMIT }, (_, index) =>
  `key-${index * 8}`,
);

function measureHitPath(): { splitMs: number; decodeMs: number } {
  let started = now();
  const parsed = parseDeepSignalShard(shardText);
  const splitMs = now() - started;
  started = now();
  let decoded = 0;
  for (const key of wantedKeys) {
    if (decodeDeepSignalRecord(parsed.get(key)!)) decoded += 1;
  }
  const decodeMs = now() - started;
  if (decoded !== wantedKeys.length) throw new Error("benchmark did not decode a full album");
  return { splitMs, decodeMs };
}

function measureMissPath(): { encodeMs: number; serializeMs: number } {
  let started = now();
  const encoded = wantedKeys.map((_, index) => encodeDeepSignalRecord(recordFor(index)));
  const encodeMs = now() - started;
  const next = new Map(shardEntries);
  wantedKeys.forEach((key, index) => next.set(key, encoded[index]));
  started = now();
  const text = serializeDeepSignalShard(next);
  const serializeMs = now() - started;
  if (text.length < shardText.length / 2) throw new Error("benchmark serialized a truncated shard");
  return { encodeMs, serializeMs };
}

measureHitPath();
measureMissPath();
const hits = Array.from({ length: ROUNDS }, measureHitPath);
const misses = Array.from({ length: ROUNDS }, measureMissPath);
const splitMs = median(hits.map((sample) => sample.splitMs));
const decodeMs = median(hits.map((sample) => sample.decodeMs));
const encodeMs = median(misses.map((sample) => sample.encodeMs));
const serializeMs = median(misses.map((sample) => sample.serializeMs));

// --- 3. How often a hit happens --------------------------------------------
//
// The candidate cap ranks the WHOLE picked set on cheap probes, so the top 64
// is a property of the filter, not of the photographs. Four filters over one
// library, and the overlap with the first one's candidates is the hit rate a
// warm store would actually see.

function library(count: number, offset = 0): PickedPhoto[] {
  return Array.from({ length: count }, (_, index): PickedPhoto => ({
    id: `asset-${index + offset}`,
    uri: `asset://asset-${index + offset}`,
    filename: `IMG_${String(index + offset).padStart(5, "0")}.jpg`,
    source: "device-gallery",
    creationTime: Date.UTC(2026, 0, 1) + (index + offset) * 90_000,
    width: 4_000,
    height: 3_000,
    placeKey: `place-${(index + offset) % 7}`,
  }));
}

function probed(photos: readonly PickedPhoto[]): ProbedCandidate[] {
  return photos.map((photo) => {
    const seed = Number(photo.id.slice("asset-".length));
    return { photo, quality: qualityFromBlurhash(blurhashFor(seed)) };
  });
}

function candidateIds(photos: readonly PickedPhoto[]): Set<string> {
  return new Set(
    chooseHeavyAnalysisCandidates(
      probed(photos),
      HEAVY_ANALYSIS_CANDIDATE_LIMIT,
    ).map(({ photo }: ProbedCandidate) => photo.id),
  );
}

const baseline = candidateIds(library(LIBRARY));
const overlaps = {
  /** The same filter again. This is the case M2 exists for. */
  repeat: candidateIds(library(LIBRARY)),
  /** Same event, plus a day of new photographs on the end. */
  grown: candidateIds(library(LIBRARY + 300)),
  /** A window slid halfway along: half the photographs are shared. */
  slid: candidateIds(library(LIBRARY, LIBRARY / 2)),
  /** A different month entirely. The floor. */
  disjoint: candidateIds(library(LIBRARY, LIBRARY * 4)),
};
const hitRate = Object.fromEntries(
  Object.entries(overlaps).map(([name, ids]) => [
    name,
    [...ids].filter((id) => baseline.has(id)).length / HEAVY_ANALYSIS_CANDIDATE_LIMIT,
  ]),
) as Record<keyof typeof overlaps, number>;

// --- The composed album-build wall clock -----------------------------------
//
// Device stage times from docs/DEEP-ANALYSIS-TIMING.md, with the deep stage
// replaced by (misses x 2,330 ms) + this benchmark's measured store cost. The
// substitution is the whole claim; nothing here re-measures the 2,330 ms.

function modelledBuildMs(hitFraction: number): number {
  const missing = Math.round(HEAVY_ANALYSIS_CANDIDATE_LIMIT * (1 - hitFraction));
  const storeMs =
    splitMs + decodeMs + (missing > 0 ? encodeMs + serializeMs : 0);
  return (
    DEVICE_STAGES_MS.cacheLoad +
    DEVICE_STAGES_MS.candidateProbeWarm +
    DEVICE_STAGES_MS.candidateRank +
    missing * DEVICE_DEEP_MS_PER_PHOTO +
    storeMs
  );
}

console.log(JSON.stringify({
  scope:
    "Node pure-JS. Store codec and candidate ranking are measured; the 2,330 ms/photo " +
    "deep stage and the warm probe/rank stages are quoted from the device run in " +
    "docs/DEEP-ANALYSIS-TIMING.md and substituted, not reproduced.",
  record: {
    bytes: utf8ByteLength(encodeDeepSignalRecord(recordFor(1))),
    shardRecords: SHARD_RECORDS,
    shardBytes: utf8ByteLength(shardText),
  },
  hitPathMs: {
    splitWholeShard: round(splitMs),
    decode64Records: round(decodeMs),
    total: round(splitMs + decodeMs),
    replacesDeviceMs: HEAVY_ANALYSIS_CANDIDATE_LIMIT * DEVICE_DEEP_MS_PER_PHOTO,
  },
  missPathMs: {
    encode64Records: round(encodeMs),
    serializeWholeShard: round(serializeMs),
    total: round(encodeMs + serializeMs),
    asPercentOfDeepStage: round(
      (100 * (encodeMs + serializeMs)) /
        (HEAVY_ANALYSIS_CANDIDATE_LIMIT * DEVICE_DEEP_MS_PER_PHOTO),
    ),
  },
  candidateOverlap: Object.fromEntries(
    Object.entries(hitRate).map(([name, value]) => [name, round(value)]),
  ),
  hermesCaveat:
    "The hit and miss paths above are V8 milliseconds. Hermes is materially " +
    "slower at JSON.parse -- the plan measures 6,694 ms for a 13.8 MB JSONL -- " +
    "so scale them, then note that even 30x leaves the hit path under 250 ms " +
    "against the 149,120 ms it replaces.",
  modelledAlbumBuildSeconds: {
    todayCold: round(207_090 / 1_000),
    /**
     * By arithmetic, not by measurement, and it does not agree with the plan.
     * EXPERT-PLAN section 3 records "repeat 26 s", but nothing in the current
     * pipeline caches a deep signal, so a repeat pays all 64 photographs again.
     * 21 + 3,263 + 4,046 + 64 x 2,330 is 156 s. Either the 26 s build did not
     * run deep analysis, or the number is not this pipeline's.
     */
    todayRepeat: round(
      (DEVICE_STAGES_MS.cacheLoad +
        DEVICE_STAGES_MS.candidateProbeWarm +
        DEVICE_STAGES_MS.candidateRank +
        HEAVY_ANALYSIS_CANDIDATE_LIMIT * DEVICE_DEEP_MS_PER_PHOTO) / 1_000,
    ),
    planClaimedRepeat: 26,
    storedRepeat: round(modelledBuildMs(hitRate.repeat) / 1_000),
    storedGrown: round(modelledBuildMs(hitRate.grown) / 1_000),
    storedSlid: round(modelledBuildMs(hitRate.slid) / 1_000),
    storedDisjoint: round(modelledBuildMs(hitRate.disjoint) / 1_000),
  },
}, null, 2));
