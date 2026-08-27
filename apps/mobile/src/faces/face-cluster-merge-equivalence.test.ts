import {
  DEFAULT_IDENTITY_THRESHOLD,
  DEFAULT_MERGE_THRESHOLD,
  DEFAULT_PERCEPTUAL_THRESHOLD,
  MERGE_EVIDENCE_MIN_FACES,
  SAME_PHOTO_DUPLICATE_SIMILARITY,
  TEMPORAL_MERGE_WINDOW_MS,
  extendFaceClusters,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-cluster.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { resolveConstraints, type FaceConstraint } from "./face-constraints.ts";
import type { FaceEmbeddingKind, Person } from "./types";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`face-cluster merge equivalence failed: ${message}`);
}

type StoredPerson = Person & {
  weightSum: number;
  firstAt?: number;
  lastAt?: number;
};

type ReferencePerson = StoredPerson & {
  assetIdSet: Set<string>;
  inverse: number;
};

type MergeOptions = {
  constraints: readonly FaceConstraint[];
  threshold: number;
  identityMergeThreshold: number;
  perceptualThreshold: number;
  evidencedMergeThreshold: number;
  temporalMergeThreshold: number;
};

function sharesAsset(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  const [smaller, larger] = a.size <= b.size ? [a, b] : [b, a];
  for (const assetId of smaller) {
    if (larger.has(assetId)) return true;
  }
  return false;
}

function intersects(a: ReadonlySet<number>, b: ReadonlySet<number>): boolean {
  const [smaller, larger] = a.size <= b.size ? [a, b] : [b, a];
  for (const value of smaller) {
    if (larger.has(value)) return true;
  }
  return false;
}

function comparisonInverse(values: number[]): number {
  let squared = 0;
  for (const value of values) {
    if (!Number.isFinite(value)) return 0;
    squared += value * value;
  }
  if (!Number.isFinite(squared) || squared === 0) return 0;
  return 1 / Math.max(1, Math.sqrt(squared));
}

function scaledSimilarity(
  a: number[],
  aInverse: number,
  b: number[],
  bInverse: number,
): number {
  if (aInverse === 0 || bInverse === 0) return 0;
  if (a.length === 0 || a.length !== b.length) return 0;
  let dot = 0;
  for (let index = 0; index < a.length; index += 1) {
    dot += a[index] * b[index];
  }
  return dot * aInverse * bInverse;
}

function spanGap(
  a: ReferencePerson,
  b: ReferencePerson,
): number | undefined {
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

function widenSpan(person: ReferencePerson, capturedAt: number | undefined): void {
  if (!Number.isFinite(capturedAt)) return;
  const at = capturedAt as number;
  person.firstAt = person.firstAt === undefined ? at : Math.min(person.firstAt, at);
  person.lastAt = person.lastAt === undefined ? at : Math.max(person.lastAt, at);
}

function mutablePerson(person: StoredPerson): ReferencePerson {
  return {
    ...person,
    assetIds: person.assetIds.slice(),
    centroid: person.centroid.slice(),
    assetIdSet: new Set(person.assetIds),
    inverse: comparisonInverse(person.centroid),
  };
}

function absorbCurrent(
  people: ReferencePerson[],
  origins: Array<Set<number>>,
  blocked: Array<Set<number>>,
  keepIndex: number,
  dropIndex: number,
  onMerge: (absorbedPersonId: string, survivingPersonId: string) => void,
): void {
  const survivor = people[keepIndex];
  const absorbed = people[dropIndex];
  const totalWeight = survivor.weightSum + absorbed.weightSum;
  survivor.centroid = survivor.centroid.map(
    (value, index) =>
      (value * survivor.weightSum + absorbed.centroid[index] * absorbed.weightSum) /
      totalWeight,
  );
  survivor.inverse = comparisonInverse(survivor.centroid);
  survivor.faceCount += absorbed.faceCount;
  survivor.weightSum += absorbed.weightSum;
  widenSpan(survivor, absorbed.firstAt);
  widenSpan(survivor, absorbed.lastAt);
  for (const assetId of absorbed.assetIds) {
    if (!survivor.assetIdSet.has(assetId)) {
      survivor.assetIdSet.add(assetId);
      survivor.assetIds.push(assetId);
    }
  }
  for (const origin of origins[dropIndex]) origins[keepIndex].add(origin);
  for (const origin of blocked[dropIndex]) blocked[keepIndex].add(origin);
  onMerge(absorbed.id, survivor.id);
  people.splice(dropIndex, 1);
  origins.splice(dropIndex, 1);
  blocked.splice(dropIndex, 1);
}

/** Frozen copy of the pre-optimization full-sweep algorithm. */
function runCurrentAlgorithm(
  input: StoredPerson[],
  opts: MergeOptions,
  onMerge: (absorbedPersonId: string, survivingPersonId: string) => void,
): ReferencePerson[] {
  const people = input.map(mutablePerson);
  const identityMergeThreshold = Math.max(
    opts.threshold,
    opts.identityMergeThreshold,
  );
  const evidencedMergeThreshold = Math.min(
    identityMergeThreshold,
    opts.evidencedMergeThreshold,
  );
  const temporalMergeThreshold = Math.min(
    evidencedMergeThreshold,
    opts.temporalMergeThreshold,
  );
  const comparable = (a: ReferencePerson, b: ReferencePerson): boolean =>
    a.embeddingKind === b.embeddingKind &&
    a.centroid.length > 0 &&
    a.centroid.length === b.centroid.length;
  const linkage = (a: ReferencePerson, b: ReferencePerson): number =>
    scaledSimilarity(a.centroid, a.inverse, b.centroid, b.inverse);
  const pairKey = (a: ReferencePerson, b: ReferencePerson): string =>
    a.id < b.id ? `${a.id} ${b.id}` : `${b.id} ${a.id}`;

  const origins = people.map((_person, index) => new Set([index]));
  const blocked = people.map(() => new Set<number>());
  for (let i = 0; i < people.length; i += 1) {
    for (let j = i + 1; j < people.length; j += 1) {
      const a = people[i];
      const b = people[j];
      if (!comparable(a, b)) continue;
      if (!sharesAsset(a.assetIdSet, b.assetIdSet)) continue;
      // No similarity escape: co-occurrence is an absolute cannot-link. The
      // mirror case is removed earlier by `dedupeFaceObservations`, so anything
      // reaching here is two surviving faces in one frame.
      blocked[i].add(j);
      blocked[j].add(i);
    }
  }

  // Same bars `mergeBars` hands the shipped path, so a face-anchored
  // constraint would resolve identically on both sides of the comparison.
  const resolved = resolveConstraints(people, opts.constraints, {
    assignment: opts.threshold,
    perceptual: opts.perceptualThreshold,
  });
  for (const [i, j] of resolved.cannot) {
    blocked[i].add(j);
    blocked[j].add(i);
  }
  const forced = resolved.must.map(
    ([ai, bi]) => [people[ai]?.id, people[bi]?.id] as const,
  );
  for (const [aId, bId] of forced) {
    if (!aId || !bId) continue;
    const i = people.findIndex((person) => person.id === aId);
    const j = people.findIndex((person) => person.id === bId);
    if (i === -1 || j === -1 || i === j) continue;
    if (!comparable(people[i], people[j])) continue;
    const [keep, drop] = i < j ? [i, j] : [j, i];
    absorbCurrent(people, origins, blocked, keep, drop, onMerge);
  }

  for (;;) {
    let bestI = -1;
    let bestJ = -1;
    let bestSimilarity = Number.NEGATIVE_INFINITY;
    for (let i = 0; i < people.length; i += 1) {
      for (let j = i + 1; j < people.length; j += 1) {
        const a = people[i];
        const b = people[j];
        if (!comparable(a, b)) continue;
        const evidenced =
          a.faceCount >= MERGE_EVIDENCE_MIN_FACES &&
          b.faceCount >= MERGE_EVIDENCE_MIN_FACES;
        const gap = spanGap(a, b);
        const nearInTime = gap !== undefined && gap <= TEMPORAL_MERGE_WINDOW_MS;
        const identityBar = evidenced
          ? nearInTime
            ? Math.min(evidencedMergeThreshold, temporalMergeThreshold)
            : evidencedMergeThreshold
          : identityMergeThreshold;
        const threshold =
          a.embeddingKind === "identity" ? identityBar : opts.perceptualThreshold;
        if (intersects(blocked[i], origins[j])) continue;
        const similarity = linkage(a, b);
        if (similarity < threshold || similarity < bestSimilarity) continue;
        if (
          similarity === bestSimilarity &&
          bestI !== -1 &&
          pairKey(a, b) >= pairKey(people[bestI], people[bestJ])
        ) {
          continue;
        }
        bestSimilarity = similarity;
        bestI = i;
        bestJ = j;
      }
    }
    if (bestI === -1) return people;
    absorbCurrent(people, origins, blocked, bestI, bestJ, onMerge);
  }
}

function makeLcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(1664525, state) + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

function randomInt(random: () => number, min: number, max: number): number {
  return min + Math.floor(random() * (max - min + 1));
}

function randomCase(
  random: () => number,
  caseIndex: number,
): { people: StoredPerson[]; options: MergeOptions } {
  const count = caseIndex === 0 ? 2 : caseIndex === 1 ? 80 : randomInt(random, 2, 80);
  const directionCount = randomInt(random, 2, Math.max(2, Math.ceil(count / 3)));
  const people: StoredPerson[] = [];
  for (let index = 0; index < count; index += 1) {
    const faceCount = randomInt(random, 1, 12);
    const direction = randomInt(random, 0, directionCount - 1);
    const exactTie = random() < 0.3;
    const angle =
      (direction * Math.PI * 2) / directionCount +
      (exactTie ? 0 : (random() - 0.5) * 0.5);
    const scale = exactTie
      ? [0.86, 0.93, 1][randomInt(random, 0, 2)]
      : 0.82 + random() * 0.18;
    const assetIds = Array.from(
      { length: faceCount },
      (_unused, faceIndex) => `case-${caseIndex}-person-${index}-face-${faceIndex}`,
    );
    if (faceCount > 1 && random() < 0.45) {
      assetIds[faceCount - 1] =
        `case-${caseIndex}-shared-${randomInt(random, 0, Math.max(1, Math.floor(count / 5)))}`;
    }
    const hasCaptureTime = random() >= 0.18;
    const firstAt = hasCaptureTime
      ? randomInt(random, 0, 720) * 24 * 60 * 60 * 1000
      : undefined;
    const lastAt =
      firstAt === undefined
        ? undefined
        : firstAt + randomInt(random, 0, 100) * 24 * 60 * 60 * 1000;
    const embeddingKind: FaceEmbeddingKind =
      random() < 0.72 ? "identity" : "perceptual";
    people.push({
      id: `person-${index + 1}`,
      faceCount,
      assetIds,
      centroid: [Math.cos(angle) * scale, Math.sin(angle) * scale],
      embeddingKind,
      weightSum: faceCount * (0.3 + random() * 0.7),
      firstAt,
      lastAt,
    });
  }

  const constraints: FaceConstraint[] = [];
  const constraintCount = randomInt(random, 0, Math.min(5, count - 1));
  for (let index = 0; index < constraintCount; index += 1) {
    const a = randomInt(random, 0, count - 1);
    let b = randomInt(random, 0, count - 2);
    if (b >= a) b += 1;
    constraints.push({
      kind: random() < 0.45 ? "must" : "cannot",
      a: people[a].assetIds[0],
      b: people[b].assetIds[0],
    });
  }

  return {
    people,
    options: {
      constraints,
      threshold: 0.25 + random() * 0.35,
      identityMergeThreshold: 0.4 + random() * 0.45,
      perceptualThreshold: 0.4 + random() * 0.45,
      evidencedMergeThreshold: 0.25 + random() * 0.5,
      temporalMergeThreshold: 0.2 + random() * 0.5,
    },
  };
}

function membershipTracker(people: StoredPerson[]): {
  onMerge: (absorbedPersonId: string, survivingPersonId: string) => void;
  members: Map<string, Set<string>>;
} {
  const members = new Map(
    people.map((person) => [
      person.id,
      new Set(
        Array.from(
          { length: person.faceCount },
          (_unused, faceIndex) => `${person.id}#face-${faceIndex}`,
        ),
      ),
    ]),
  );
  return {
    members,
    onMerge: (absorbedPersonId, survivingPersonId) => {
      const survivor = members.get(survivingPersonId);
      const absorbed = members.get(absorbedPersonId);
      assert(survivor && absorbed, "merge callback named a missing person");
      for (const member of absorbed) survivor.add(member);
      members.delete(absorbedPersonId);
    },
  };
}

const CASE_COUNT = 240;
const SEED = 0xc0de_11;
const random = makeLcg(SEED);
for (let caseIndex = 0; caseIndex < CASE_COUNT; caseIndex += 1) {
  const { people, options } = randomCase(random, caseIndex);
  const currentMembership = membershipTracker(people);
  const nextMembership = membershipTracker(people);
  const current = runCurrentAlgorithm(people, options, currentMembership.onMerge);
  const next = extendFaceClusters(people, [], {
    ...options,
    onMerge: nextMembership.onMerge,
  });

  assert(
    next.length === current.length,
    `seed=${SEED} case=${caseIndex}: count ${next.length} !== ${current.length}`,
  );
  const currentIds = current.map((person) => person.id);
  const nextIds = next.map((person) => person.id);
  assert(
    JSON.stringify(nextIds) === JSON.stringify(currentIds),
    `seed=${SEED} case=${caseIndex}: surviving order ${nextIds} !== ${currentIds}`,
  );
  for (let index = 0; index < current.length; index += 1) {
    const id = current[index].id;
    const expected = [...(currentMembership.members.get(id) ?? [])].sort();
    const actual = [...(nextMembership.members.get(id) ?? [])].sort();
    assert(
      JSON.stringify(actual) === JSON.stringify(expected),
      `seed=${SEED} case=${caseIndex} survivor=${id}: members ${actual} !== ${expected}`,
    );
  }
}

// Defaults are deliberately referenced so this copy fails loudly when the
// production bars change without the equivalence harness being reviewed.
assert(DEFAULT_IDENTITY_THRESHOLD > 0, "identity default must be valid");
assert(DEFAULT_MERGE_THRESHOLD > 0, "merge default must be valid");
assert(DEFAULT_PERCEPTUAL_THRESHOLD > 0, "perceptual default must be valid");

// eslint-disable-next-line no-console
console.log(`face-cluster merge equivalence passed (${CASE_COUNT} cases, seed=${SEED})`);
