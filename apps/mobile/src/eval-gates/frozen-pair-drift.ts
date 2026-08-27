// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { existsSync, readFileSync, statSync } from "node:fs";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { basename, dirname, join } from "node:path";

// @ts-expect-error Node's native TypeScript runner requires source extensions.
import {
  DEFAULT_MERGE_THRESHOLD,
  SAME_PHOTO_EXCEPTION_SIMILARITY,
  cosine,
} from "../faces/face-cluster.ts";
// @ts-expect-error Node's native TypeScript runner requires source extensions.
import { round, type GateResult } from "./gate-report.ts";

const SHORT_GAP_MS = 60 * 24 * 60 * 60 * 1000;
const MAX_PAIRS_PER_CATEGORY = 5_000;
const MIN_PAIRS_PER_CATEGORY = 20;
const MIN_NEGATIVES_FOR_FAR_POINT_ONE_PERCENT = 1_000;
const MIN_DIMENSIONS = 8;
const BAR_SENTINEL_DISTANCE = 0.035;

export type PairCategory =
  | "same-person-short-gap"
  | "same-person-long-gap"
  | "same-photo-negative"
  | "ordinary-negative"
  | "low-quality-profile-positive";

type EvaluationObservation = {
  id: string;
  assetId: string;
  personId?: string;
  capturedAt?: number;
  seedable?: boolean;
  embedding: number[];
};

type EvaluationIndex = {
  modelRevision: string;
  threshold: number;
  observations: EvaluationObservation[];
};

type FrozenPair = {
  key: string;
  leftId: string;
  rightId: string;
  category: PairCategory;
  positive: boolean;
};

type ScoredPair = FrozenPair & {
  score: number;
};

type VerificationMetrics = {
  rocAuc: number;
  tarAtFar1Percent: number;
  tarAtFarPoint1Percent: number;
  eer: number;
  positives: number;
  negatives: number;
};

type CrossingMeasurement = {
  bar: number;
  crossedUp: number;
  crossedDown: number;
  total: number;
};

export type FrozenPairMeasurements = {
  currentModelRevision: string;
  previousModelRevision: string;
  categories: Record<PairCategory, number>;
  current: VerificationMetrics;
  previous: VerificationMetrics;
  crossings: Record<string, CrossingMeasurement>;
  totalCrossings: number;
  pairCount: number;
  embeddingDimensions: number;
};

export function runFrozenPairDriftGate(
  currentIndexPath: string,
  previousIndexPath: string,
): GateResult<FrozenPairMeasurements> {
  const current = loadEvaluationIndex(currentIndexPath);
  const previous = loadEvaluationIndex(previousIndexPath);
  const pairs = freezePairs(current.observations);
  const currentScores = scorePairs(pairs, current.observations);
  const previousScores = scorePairs(pairs, previous.observations);
  const previousByKey = new Map(previousScores.map((pair) => [pair.key, pair]));
  const missingPrevious = currentScores.filter(
    (pair) => !previousByKey.has(pair.key),
  );
  const categories = categoryCounts(pairs);
  const bars = {
    assignment: current.threshold,
    merge: DEFAULT_MERGE_THRESHOLD,
    samePhotoException: SAME_PHOTO_EXCEPTION_SIMILARITY,
  };
  const crossings = Object.fromEntries(
    Object.entries(bars).map(([name, bar]) => [
      name,
      crossingCount(currentScores, previousByKey, bar),
    ]),
  ) as Record<string, CrossingMeasurement>;
  const totalCrossings = currentScores.filter((pair) => {
    const previous = previousByKey.get(pair.key);
    return (
      previous !== undefined &&
      Object.values(bars).some(
        (bar) => (previous.score >= bar) !== (pair.score >= bar),
      )
    );
  }).length;
  const currentMetrics = verificationMetrics(currentScores);
  const previousMetrics = verificationMetrics(previousScores);
  const dimensions = current.observations[0]?.embedding.length ?? 0;
  const enoughCategories = Object.values(categories).every(
    (count) => count >= MIN_PAIRS_PER_CATEGORY,
  );
  const enoughNegatives =
    currentMetrics.negatives >= MIN_NEGATIVES_FOR_FAR_POINT_ONE_PERCENT;
  const validEmbeddings =
    dimensions >= MIN_DIMENSIONS &&
    current.observations.every(
      (observation) =>
        observation.embedding.length === dimensions &&
        observation.embedding.every(Number.isFinite) &&
        Math.hypot(...observation.embedding) > Number.EPSILON,
    );
  const sentinels = Object.entries(bars).map(([name, bar]) => ({
    name,
    present: currentScores.some(
      (pair) => Math.abs(pair.score - bar) <= BAR_SENTINEL_DISTANCE,
    ),
  }));
  const sentinelCoverage = sentinels.every(({ present }) => present);
  const vacuityPassed =
    pairs.length > 0 &&
    enoughCategories &&
    enoughNegatives &&
    validEmbeddings &&
    missingPrevious.length === 0 &&
    sentinelCoverage;
  const violations = [
    ...(totalCrossings > 0
      ? Object.entries(crossings)
          .filter(([, value]) => value.total > 0)
          .map(
            ([name, value]) =>
              `${name}@${value.bar.toFixed(3)}: ${value.crossedUp} up, ${value.crossedDown} down`,
          )
      : []),
    ...(missingPrevious.length > 0
      ? [`${missingPrevious.length} frozen pairs are absent from the previous export.`]
      : []),
  ];
  const passed = vacuityPassed && totalCrossings === 0;

  return {
    gate: "GATE 3 — frozen-pair drift",
    status: passed ? "PASS" : "FAIL",
    summary:
      `${pairs.length} frozen pairs; ROC AUC ${currentMetrics.rocAuc.toFixed(4)}, ` +
      `TAR@FAR 1% ${(currentMetrics.tarAtFar1Percent * 100).toFixed(2)}%, ` +
      `TAR@FAR 0.1% ${(currentMetrics.tarAtFarPoint1Percent * 100).toFixed(2)}%, ` +
      `EER ${(currentMetrics.eer * 100).toFixed(2)}%; ` +
      `${totalCrossings} pairs crossed at least one current bar.`,
    measurements: {
      currentModelRevision: current.modelRevision,
      previousModelRevision: previous.modelRevision,
      categories,
      current: currentMetrics,
      previous: previousMetrics,
      crossings,
      totalCrossings,
      pairCount: pairs.length,
      embeddingDimensions: dimensions,
    },
    vacuityGuard: {
      passed: vacuityPassed,
      detail:
        `each category >=${MIN_PAIRS_PER_CATEGORY}=${enoughCategories}; ` +
        `negatives >=${MIN_NEGATIVES_FOR_FAR_POINT_ONE_PERCENT}=${enoughNegatives}; ` +
        `embedding dimensions >=${MIN_DIMENSIONS} and valid=${validEmbeddings}; ` +
        `all frozen pairs exist in previous=${missingPrevious.length === 0}; ` +
        `pairs within ${BAR_SENTINEL_DISTANCE} of every current bar=${sentinelCoverage} ` +
        `(${sentinels.map(({ name, present }) => `${name}:${present}`).join(", ")}).`,
    },
    violations,
  };
}

export function loadEvaluationIndex(path: string): EvaluationIndex {
  const indexPath = statSync(path).isDirectory() ? join(path, "face-index.json") : path;
  const parsed: unknown = JSON.parse(readFileSync(indexPath, "utf8"));
  if (!isRecord(parsed)) throw new Error(`Invalid face index JSON: ${indexPath}`);
  if (parsed.format === "photeo-synthetic-face-index-v1") {
    return generateSyntheticIndex(parsed, basename(indexPath));
  }
  if (parsed.format === "photeo-face-eval-index-v1") {
    return readExplicitEvaluationIndex(parsed, basename(indexPath));
  }
  return readPersistedFaceIndex(parsed, indexPath);
}

function freezePairs(observations: readonly EvaluationObservation[]): FrozenPair[] {
  const sorted = [...observations].sort((left, right) =>
    left.id.localeCompare(right.id),
  );
  const byCategory = emptyCategoryLists<FrozenPair>();
  for (let leftIndex = 0; leftIndex < sorted.length; leftIndex += 1) {
    for (
      let rightIndex = leftIndex + 1;
      rightIndex < sorted.length;
      rightIndex += 1
    ) {
      const left = sorted[leftIndex];
      const right = sorted[rightIndex];
      const category = pairCategory(left, right);
      if (!category || byCategory[category].length >= MAX_PAIRS_PER_CATEGORY) {
        continue;
      }
      const positive = category !== "same-photo-negative" && category !== "ordinary-negative";
      byCategory[category].push({
        key: `${left.id}\u0000${right.id}`,
        leftId: left.id,
        rightId: right.id,
        category,
        positive,
      });
    }
  }
  return (Object.keys(byCategory) as PairCategory[]).flatMap(
    (category) => byCategory[category],
  );
}

function pairCategory(
  left: EvaluationObservation,
  right: EvaluationObservation,
): PairCategory | undefined {
  if (left.assetId === right.assetId) return "same-photo-negative";
  if (left.personId && right.personId && left.personId === right.personId) {
    if (left.seedable === false || right.seedable === false) {
      return "low-quality-profile-positive";
    }
    if (
      Number.isFinite(left.capturedAt) &&
      Number.isFinite(right.capturedAt)
    ) {
      return Math.abs(left.capturedAt! - right.capturedAt!) <= SHORT_GAP_MS
        ? "same-person-short-gap"
        : "same-person-long-gap";
    }
    return undefined;
  }
  if (left.personId && right.personId && left.personId !== right.personId) {
    return "ordinary-negative";
  }
  return undefined;
}

function scorePairs(
  pairs: readonly FrozenPair[],
  observations: readonly EvaluationObservation[],
): ScoredPair[] {
  const byId = new Map(observations.map((observation) => [observation.id, observation]));
  return pairs.flatMap((pair) => {
    const left = byId.get(pair.leftId);
    const right = byId.get(pair.rightId);
    if (!left || !right) return [];
    return [{ ...pair, score: cosine(left.embedding, right.embedding) }];
  });
}

function verificationMetrics(pairs: readonly ScoredPair[]): VerificationMetrics {
  const positives = pairs.filter((pair) => pair.positive);
  const negatives = pairs.filter((pair) => !pair.positive);
  return {
    rocAuc: round(rocAuc(pairs)),
    tarAtFar1Percent: round(tarAtFar(positives, negatives, 0.01)),
    tarAtFarPoint1Percent: round(tarAtFar(positives, negatives, 0.001)),
    eer: round(eer(positives, negatives)),
    positives: positives.length,
    negatives: negatives.length,
  };
}

/** Mann-Whitney form of ROC AUC, with average ranks for ties. */
function rocAuc(pairs: readonly ScoredPair[]): number {
  const sorted = [...pairs].sort((left, right) => left.score - right.score);
  const positiveCount = sorted.filter((pair) => pair.positive).length;
  const negativeCount = sorted.length - positiveCount;
  if (positiveCount === 0 || negativeCount === 0) return 0;
  let positiveRankTotal = 0;
  for (let start = 0; start < sorted.length; ) {
    let end = start + 1;
    while (end < sorted.length && sorted[end].score === sorted[start].score) {
      end += 1;
    }
    // Ranks are one-based; tied observations receive the group's average rank.
    const averageRank = (start + 1 + end) / 2;
    const positivesInGroup = sorted
      .slice(start, end)
      .filter((pair) => pair.positive).length;
    positiveRankTotal += averageRank * positivesInGroup;
    start = end;
  }
  return (
    (positiveRankTotal - (positiveCount * (positiveCount + 1)) / 2) /
    (positiveCount * negativeCount)
  );
}

function tarAtFar(
  positives: readonly ScoredPair[],
  negatives: readonly ScoredPair[],
  targetFar: number,
): number {
  if (positives.length === 0 || negatives.length === 0) return 0;
  const thresholds = [
    Number.POSITIVE_INFINITY,
    ...new Set([...positives, ...negatives].map((pair) => pair.score)),
  ].sort((left, right) => right - left);
  let best = 0;
  for (const threshold of thresholds) {
    const far = negatives.filter((pair) => pair.score >= threshold).length / negatives.length;
    if (far > targetFar + Number.EPSILON) continue;
    const tar = positives.filter((pair) => pair.score >= threshold).length / positives.length;
    best = Math.max(best, tar);
  }
  return best;
}

function eer(
  positives: readonly ScoredPair[],
  negatives: readonly ScoredPair[],
): number {
  if (positives.length === 0 || negatives.length === 0) return 1;
  const thresholds = [
    Number.POSITIVE_INFINITY,
    ...new Set([...positives, ...negatives].map((pair) => pair.score)),
    Number.NEGATIVE_INFINITY,
  ].sort((left, right) => right - left);
  let best = { distance: Number.POSITIVE_INFINITY, value: 1 };
  for (const threshold of thresholds) {
    const far = negatives.filter((pair) => pair.score >= threshold).length / negatives.length;
    const falseReject = positives.filter((pair) => pair.score < threshold).length / positives.length;
    const distance = Math.abs(far - falseReject);
    if (distance < best.distance) {
      best = { distance, value: (far + falseReject) / 2 };
    }
  }
  return best.value;
}

function crossingCount(
  current: readonly ScoredPair[],
  previousByKey: ReadonlyMap<string, ScoredPair>,
  bar: number,
): CrossingMeasurement {
  let crossedUp = 0;
  let crossedDown = 0;
  for (const pair of current) {
    const previous = previousByKey.get(pair.key);
    if (!previous) continue;
    if (previous.score < bar && pair.score >= bar) crossedUp += 1;
    if (previous.score >= bar && pair.score < bar) crossedDown += 1;
  }
  return { bar, crossedUp, crossedDown, total: crossedUp + crossedDown };
}

function categoryCounts(
  pairs: readonly FrozenPair[],
): Record<PairCategory, number> {
  const counts = emptyCategoryCounts();
  for (const pair of pairs) counts[pair.category] += 1;
  return counts;
}

function emptyCategoryCounts(): Record<PairCategory, number> {
  return {
    "same-person-short-gap": 0,
    "same-person-long-gap": 0,
    "same-photo-negative": 0,
    "ordinary-negative": 0,
    "low-quality-profile-positive": 0,
  };
}

function emptyCategoryLists<T>(): Record<PairCategory, T[]> {
  return {
    "same-person-short-gap": [],
    "same-person-long-gap": [],
    "same-photo-negative": [],
    "ordinary-negative": [],
    "low-quality-profile-positive": [],
  };
}

function generateSyntheticIndex(
  value: Record<string, unknown>,
  fallbackRevision: string,
): EvaluationIndex {
  const people = integer(value.people);
  const dimensions = integer(value.dimensions);
  const seed = integer(value.seed);
  const threshold = finiteNumber(value.threshold);
  if (people < 10 || dimensions < MIN_DIMENSIONS) {
    throw new Error("Synthetic face index requires >=10 people and >=8 dimensions.");
  }
  const random = seededRandom(seed);
  const observations: EvaluationObservation[] = [];
  const start = Date.UTC(2020, 0, 1);
  const bases = Array.from({ length: people }, () => randomUnit(dimensions, random));
  for (let person = 0; person < people; person += 1) {
    const personId = `person-${String(person + 1).padStart(3, "0")}`;
    const paired = Math.floor(person / 2);
    const definitions = [
      { name: "early", days: 0, noise: 0.05, seedable: true, asset: `solo-${personId}-early` },
      { name: "short", days: 20, noise: 0.15, seedable: true, asset: `solo-${personId}-short` },
      { name: "long", days: 400, noise: 0.9, seedable: true, asset: `solo-${personId}-long` },
      { name: "profile", days: 30, noise: 1.5, seedable: false, asset: `solo-${personId}-profile` },
      { name: "group", days: 10, noise: 0.3, seedable: true, asset: `group-${paired}` },
    ];
    for (const definition of definitions) {
      observations.push({
        id: `${personId}-${definition.name}`,
        assetId: definition.asset,
        personId,
        capturedAt: start + definition.days * 24 * 60 * 60 * 1000,
        seedable: definition.seedable,
        embedding: noisyEmbedding(bases[person], definition.noise, random),
      });
    }
  }
  return {
    modelRevision:
      typeof value.modelRevision === "string" ? value.modelRevision : fallbackRevision,
    threshold,
    observations,
  };
}

function readExplicitEvaluationIndex(
  value: Record<string, unknown>,
  fallbackRevision: string,
): EvaluationIndex {
  if (!Array.isArray(value.observations)) {
    throw new Error("Evaluation face index is missing observations.");
  }
  return {
    modelRevision:
      typeof value.modelRevision === "string" ? value.modelRevision : fallbackRevision,
    threshold: finiteNumber(value.threshold),
    observations: value.observations.map((observation, index) =>
      explicitObservation(observation, index),
    ),
  };
}

function readPersistedFaceIndex(
  value: Record<string, unknown>,
  indexPath: string,
): EvaluationIndex {
  const threshold = finiteNumber(value.threshold);
  if (!Array.isArray(value.people)) {
    throw new Error(`Persisted face index has no people array: ${indexPath}`);
  }
  const owners = assetOwners(value.people);
  let rawObservations: unknown[];
  if (Array.isArray(value.observations)) {
    rawObservations = value.observations;
  } else {
    const observationsPath = join(dirname(indexPath), "face-observations.jsonl");
    if (!existsSync(observationsPath)) {
      throw new Error(
        `Face observations are absent. Export face-index.json and its sibling ` +
          `face-observations.jsonl together (looked beside ${indexPath}).`,
      );
    }
    rawObservations = readFileSync(observationsPath, "utf8")
      .split("\n")
      .filter((line: string) => line.trim().length > 0)
      .map((line: string) => JSON.parse(line));
  }
  const occurrences = new Map<string, number>();
  const observations = rawObservations.map((raw, index) => {
    if (!isRecord(raw) || typeof raw.assetId !== "string") {
      throw new Error(`Invalid persisted observation at position ${index}.`);
    }
    const occurrence = occurrences.get(raw.assetId) ?? 0;
    occurrences.set(raw.assetId, occurrence + 1);
    const embedding = readEmbedding(raw.embedding);
    return {
      id: `${raw.assetId}#${occurrence}`,
      assetId: raw.assetId,
      personId: owners.get(raw.assetId),
      capturedAt:
        typeof raw.capturedAt === "number" && Number.isFinite(raw.capturedAt)
          ? raw.capturedAt
          : undefined,
      seedable: raw.seedable === false ? false : true,
      embedding,
    };
  });
  return {
    modelRevision: `face-index-v${String(value.version ?? "unknown")}`,
    threshold,
    observations,
  };
}

function assetOwners(people: unknown[]): Map<string, string> {
  const candidates = new Map<string, Set<string>>();
  for (const person of people) {
    if (!isRecord(person) || typeof person.id !== "string" || !Array.isArray(person.assetIds)) {
      continue;
    }
    for (const assetId of person.assetIds) {
      if (typeof assetId !== "string") continue;
      const set = candidates.get(assetId) ?? new Set<string>();
      set.add(person.id);
      candidates.set(assetId, set);
    }
  }
  return new Map(
    [...candidates.entries()].flatMap(([assetId, peopleForAsset]) =>
      peopleForAsset.size === 1 ? [[assetId, [...peopleForAsset][0]] as const] : [],
    ),
  );
}

function explicitObservation(value: unknown, index: number): EvaluationObservation {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.assetId !== "string") {
    throw new Error(`Invalid explicit evaluation observation at position ${index}.`);
  }
  return {
    id: value.id,
    assetId: value.assetId,
    personId: typeof value.personId === "string" ? value.personId : undefined,
    capturedAt:
      typeof value.capturedAt === "number" && Number.isFinite(value.capturedAt)
        ? value.capturedAt
        : undefined,
    seedable: value.seedable === false ? false : true,
    embedding: readEmbedding(value.embedding),
  };
}

function readEmbedding(value: unknown): number[] {
  if (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((component) => typeof component === "number" && Number.isFinite(component))
  ) {
    return value as number[];
  }
  if (typeof value === "string" && value.length > 0) {
    const binary = globalThis.atob(value);
    return Array.from(binary, (character) => {
      const byte = character.charCodeAt(0);
      return (byte > 127 ? byte - 256 : byte) / 127;
    });
  }
  throw new Error("Face observation has an invalid embedding.");
}

function noisyEmbedding(
  base: readonly number[],
  noise: number,
  random: () => number,
): number[] {
  let direction = randomUnit(base.length, random);
  const projection = direction.reduce(
    (total, value, index) => total + value * base[index],
    0,
  );
  direction = normalize(direction.map((value, index) => value - projection * base[index]));
  return normalize(base.map((value, index) => value + noise * direction[index]));
}

function randomUnit(dimensions: number, random: () => number): number[] {
  return normalize(
    Array.from({ length: dimensions }, () => gaussian(random)),
  );
}

function gaussian(random: () => number): number {
  const first = Math.max(Number.EPSILON, random());
  const second = random();
  return Math.sqrt(-2 * Math.log(first)) * Math.cos(2 * Math.PI * second);
}

function normalize(values: readonly number[]): number[] {
  const magnitude = Math.hypot(...values);
  return values.map((value) => value / magnitude);
}

function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 0x1_0000_0000;
  };
}

function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new Error("Synthetic face index contains a non-integer field.");
  }
  return value;
}

function finiteNumber(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error("Face index contains an invalid numeric field.");
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
