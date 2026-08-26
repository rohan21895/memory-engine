import type { PickedPhoto } from "../import/picked-photo";
import type { QualitySignals } from "./quality-signals";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { chooseHeavyAnalysisCandidates, type ProbedCandidate } from "./candidate-prepass.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { qualityFromBlurhash } from "./candidate-quality-probe.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { candidateProbeKey, parseCandidateProbeCache, serializeCandidateProbeCache } from "./candidate-probe-cache.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { selectBestShots } from "./select-best-shots.ts";

const PHOTO_COUNT = 3_000;
const CANDIDATE_COUNT = 64;
const ROUNDS = 7;
const BLURHASH = "LEHV6nWB2yk8pyo0adR*.7kCMdnj";

type StageTimes = {
  cacheLoad: number;
  candidateProbe: number;
  candidateRank: number;
  chooseBestShots: number;
  reviewAssembly: number;
  total: number;
};

const photos = Array.from({ length: PHOTO_COUNT }, (_, index): PickedPhoto => ({
  id: `asset-${index}`,
  uri: `asset://asset-${index}`,
  filename: `IMG_${String(index).padStart(4, "0")}.jpg`,
  source: "device-gallery",
  creationTime: Date.UTC(2026, 0, 1) + index * 1_000,
  width: 4_000,
  height: 3_000,
  placeKey: `place-${index % 7}`,
}));

const checkpointEntries = new Map(
  photos.map((photo) => [candidateProbeKey(photo), qualityFromBlurhash(BLURHASH)]),
);
const checkpoint = serializeCandidateProbeCache(checkpointEntries);

function run(cached: boolean): StageTimes {
  const totalStarted = now();
  let started = now();
  const cache = cached ? parseCandidateProbeCache(checkpoint) : new Map();
  const cacheLoad = now() - started;

  started = now();
  const probed: ProbedCandidate[] = photos.map((photo) => ({
    photo,
    quality: cache.get(candidateProbeKey(photo)) ?? qualityFromBlurhash(BLURHASH),
  }));
  const candidateProbe = now() - started;

  started = now();
  const candidates = chooseHeavyAnalysisCandidates(probed, CANDIDATE_COUNT);
  const candidateRank = now() - started;

  started = now();
  const album = selectBestShots(candidates.map(({ photo, quality }, index) => ({
    ...photo,
    embedding: fingerprint(index),
    perceptualEmbedding: fingerprint(index),
    analysis: signals(quality.sharpness ?? 0.5),
  })), { count: 24 });
  const chooseBestShots = now() - started;

  started = now();
  const uriById = new Map(photos.map((photo) => [photo.id, photo.uri]));
  const review = {
    selected: album.selected.map((item) => ({ ...item, uri: uriById.get(item.media_id) ?? "" })),
    pool: album.pool.map((item) => ({ ...item, uri: uriById.get(item.media_id) ?? "" })),
  };
  const reviewAssembly = now() - started;
  if (review.selected.length === 0 || candidates.length !== CANDIDATE_COUNT) {
    throw new Error("benchmark fixture did not exercise a full album");
  }
  return {
    cacheLoad,
    candidateProbe,
    candidateRank,
    chooseBestShots,
    reviewAssembly,
    total: now() - totalStarted,
  };
}

// Warm JIT/import paths before measuring either side.
run(false);
run(true);
const cold = Array.from({ length: ROUNDS }, () => run(false));
const warm = Array.from({ length: ROUNDS }, () => run(true));
console.log(JSON.stringify({
  scope: "Node pure-JS; native image load/hash and 64-photo model analysis excluded",
  photos: PHOTO_COUNT,
  candidates: CANDIDATE_COUNT,
  rounds: ROUNDS,
  beforeCold: medianStages(cold),
  afterCheckpointHit: medianStages(warm),
}));

function medianStages(samples: StageTimes[]): StageTimes {
  return Object.fromEntries(
    (Object.keys(samples[0]) as Array<keyof StageTimes>).map((stage) => [
      stage,
      round(median(samples.map((sample) => sample[stage]))),
    ]),
  ) as StageTimes;
}

function median(values: number[]): number {
  const sorted = values.slice().sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)];
}

function now(): number {
  return performance.now();
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

function fingerprint(seed: number): number[] {
  const values = Array.from({ length: 76 }, (_, index) =>
    Math.sin(seed * 17.3 + index * 0.71) + Math.cos(seed * 0.23 - index),
  );
  const magnitude = Math.sqrt(values.reduce((sum, value) => sum + value * value, 0));
  return values.map((value) => value / magnitude);
}

function signals(sharpness: number): QualitySignals {
  return {
    sharpness,
    exposure: 0.5,
    clippedFraction: 0,
    faces: [],
    faceCount: 0,
    largestFaceAreaRatio: 0,
    anyFaceCutAtEdge: false,
    isScreenshotOrDocument: false,
    category: "scene",
  };
}
