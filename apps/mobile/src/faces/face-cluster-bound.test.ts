import {
  ASSIGNABLE_CENTROID_WEIGHT,
  SAME_PHOTO_EXCEPTION_SIMILARITY,
  extendFaceClusters,
  updateCentroid,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-cluster.ts";

function assert(value: unknown, message: string): void {
  if (!value) throw new Error(`face-cluster bound self-check failed: ${message}`);
}

/**
 * The assignment loop now abandons a dot product as soon as Cauchy-Schwarz
 * proves it cannot win. That is only allowed to make clustering FASTER, never
 * different -- so this compares it against an INDEPENDENT reimplementation of
 * the same assignment with no early exit at all, and demands an identical
 * grouping.
 *
 * The reference below is deliberately written the slow, obvious way: every dot
 * product runs to all 512 dimensions. If the two ever disagree, the bound is
 * wrong. Comparing the optimised path against itself -- which an earlier draft
 * of this file did -- proves only that it is deterministic, which was never in
 * doubt.
 *
 * Merging is excluded (`skipMerge`) because it is not what changed, and mixing
 * it in would let a merge quietly repair an assignment mistake.
 */

const DIMS = 512;
let seed = 24681357;
function rand(): number {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return seed / 0x7fffffff;
}
function gauss(): number {
  return Math.sqrt(-2 * Math.log(rand() || 1e-9)) * Math.cos(2 * Math.PI * rand());
}
function unit(values: number[]): number[] {
  let squared = 0;
  for (const value of values) squared += value * value;
  const norm = Math.sqrt(squared) || 1;
  return values.map((value) => value / norm);
}

type Face = {
  assetId: string;
  embedding: number[];
  embeddingKind: "identity";
  capturedAt?: number;
  seedable?: boolean;
};

// ---------------------------------------------------------------------------
// Reference: greedy assignment, no early exit, no cleverness.
// ---------------------------------------------------------------------------

function magnitude(values: number[]): number {
  let squared = 0;
  for (const value of values) squared += value * value;
  return Math.sqrt(squared);
}
function toUnit(embedding: number[]): number[] {
  const length = magnitude(embedding);
  return length > Number.EPSILON && Math.abs(length - 1) > 1e-9
    ? embedding.map((value) => value / length)
    : embedding;
}
/** The full dot product, always. This is the thing the bound must not change. */
function referenceSimilarity(a: number[], b: number[]): number {
  if (a.length === 0 || a.length !== b.length) return 0;
  const aScale = 1 / Math.max(1, magnitude(a));
  const bScale = 1 / Math.max(1, magnitude(b));
  if (!Number.isFinite(aScale) || !Number.isFinite(bScale)) return 0;
  let dot = 0;
  for (let index = 0; index < a.length; index += 1) dot += a[index] * b[index];
  return dot * aScale * bScale;
}

function referenceAssign(faces: Face[], threshold: number): string[][] {
  const people: { centroid: number[]; assets: string[]; assetSet: Set<string>; weight: number }[] = [];
  for (const face of faces) {
    const embedding = toUnit(face.embedding);
    let bestIndex = -1;
    let best = Number.NEGATIVE_INFINITY;
    for (let index = 0; index < people.length; index += 1) {
      const person = people[index];
      if (embedding.length === 0 || embedding.length !== person.centroid.length) continue;
      const similarity = referenceSimilarity(embedding, person.centroid);
      if (
        person.assetSet.has(face.assetId) &&
        similarity < SAME_PHOTO_EXCEPTION_SIMILARITY
      ) {
        continue;
      }
      if (similarity >= threshold && similarity > best) {
        bestIndex = index;
        best = similarity;
      }
    }
    const weight = face.seedable === false ? ASSIGNABLE_CENTROID_WEIGHT : 1;
    if (bestIndex === -1) {
      if (face.seedable === false) continue;
      people.push({
        centroid: embedding.slice(),
        assets: [face.assetId],
        assetSet: new Set([face.assetId]),
        weight,
      });
      continue;
    }
    const person = people[bestIndex];
    person.centroid = updateCentroid(person.centroid, embedding, person.weight, weight);
    person.weight += weight;
    if (!person.assetSet.has(face.assetId)) {
      person.assetSet.add(face.assetId);
      person.assets.push(face.assetId);
    }
  }
  return people.map((person) => person.assets);
}

/** Sorted cluster contents. Two runs agree only if they grouped identically. */
function partition(groups: string[][]): string {
  return groups
    .map((assets) => assets.slice().sort().join(","))
    .sort()
    .join("|");
}

function library(identityCount: number, perIdentity: number, spread: number): Face[] {
  const faces: Face[] = [];
  for (let person = 0; person < identityCount; person += 1) {
    const base = unit(Array.from({ length: DIMS }, () => gauss()));
    for (let shot = 0; shot < perIdentity; shot += 1) {
      // Every fourth photo is a group shot two identities appear in, so the
      // same-photo cannot-link and its mirror/panorama exception are exercised
      // rather than merely present -- that pair is the bound's sharpest edge.
      const shared = shot % 4 === 0;
      faces.push({
        assetId: shared ? `group-${shot}-${person % 3}` : `p${person}-s${shot}`,
        embedding: unit(base.map((value) => value + gauss() * spread)),
        embeddingKind: "identity",
        ...(shot % 7 === 3 ? { seedable: false } : {}),
      });
    }
  }
  for (let i = faces.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rand() * (i + 1));
    [faces[i], faces[j]] = [faces[j], faces[i]];
  }
  return faces;
}

let checked = 0;
for (const spread of [0.02, 0.035, 0.06, 0.12]) {
  for (const threshold of [0.2, 0.35, 0.5, 0.7]) {
    const faces = library(9, 11, spread);
    const optimised = extendFaceClusters([], faces, { threshold, skipMerge: true });
    const reference = referenceAssign(faces, threshold);
    assert(
      partition(optimised.map((person) => person.assetIds)) === partition(reference),
      `early exit changed the grouping at spread=${spread} threshold=${threshold}: ` +
        `${optimised.length} clusters vs ${reference.length} un-pruned`,
    );
    checked += 1;
  }
}

/**
 * The mirror/panorama exception, isolated.
 *
 * A person already holding a face from this photo may only take another if the
 * similarity clears 0.72 -- far above the assignment bar. An early exit that
 * asked only for the assignment bar would abandon the dot product before it
 * could be tested against the exception, and two faces from one photo would
 * silently stop joining. These two are near-identical AND share a photo.
 */
const mirrorBase = unit(Array.from({ length: DIMS }, () => gauss()));
const mirrored = extendFaceClusters(
  [],
  [
    { assetId: "panorama-1", embedding: mirrorBase, embeddingKind: "identity" as const },
    {
      assetId: "panorama-1",
      embedding: unit(mirrorBase.map((value) => value + gauss() * 0.005)),
      embeddingKind: "identity" as const,
    },
  ],
  { threshold: 0.35, skipMerge: true },
);
assert(
  mirrored.length === 1 && mirrored[0].faceCount === 2,
  "a near-identical repeat of one face in one photo still joins it -- the early " +
    "exit must clear the same-photo exception bar, not the assignment bar",
);

console.log(
  `face-cluster bound self-check passed (${checked} libraries vs un-pruned reference, ` +
    `+ same-photo exception)`,
);
