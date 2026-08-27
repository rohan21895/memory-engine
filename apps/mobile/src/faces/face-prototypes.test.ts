// @ts-expect-error Node's TypeScript runner requires the source extension.
import { derivePrototypes, prototypeLinkage, prototypeWeightFor, maxPrototypesFor, MULTI_PROTOTYPE_ENABLED, PROTOTYPE_MIN_FACES } from "./face-prototypes.ts";
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { comparisonInverse, scaledSimilarity, ASSIGNABLE_CENTROID_WEIGHT } from "./face-cluster.ts";
// A type-only import is erased before the extension can matter, so unlike the
// value imports above it needs no suppression.
import type { PrototypeFace } from "./face-prototypes.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`face-prototypes self-check failed: ${message}`);
  }
}

/**
 * Multi-prototype identity, tested on the case it was PROPOSED for and on the
 * case it must not disturb.
 *
 * The whole module is shadow: it can only be trusted at all if it reduces
 * exactly to the shipped single centroid when nobody splits, and it can only be
 * useful if it splits a tile that genuinely holds two appearances. Both are
 * asserted below, and each assertion is followed by the input that makes it
 * FAIL — a test that a split happened proves nothing if the function splits
 * everything, and a test that a tight cluster stayed whole proves nothing if the
 * function never splits at all.
 *
 * The measured answer on the owner's real library is in
 * `scratch/multi-prototype/measure.ts`, and it is NEGATIVE: no tile there is
 * incoherent enough to have a second appearance to find. That does not make the
 * mechanism below wrong, it makes the library's tiles already-single-mode, and
 * these fixtures are the synthetic case that shows the difference is the data's
 * rather than the code's.
 */

const DIMENSIONS = 8;

/** Deterministic PRNG. A fixture that shifts between runs is not a fixture. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function unit(values: number[]): number[] {
  let squared = 0;
  for (const value of values) squared += value * value;
  const length = Math.sqrt(squared);
  return length > 0 ? values.map((value) => value / length) : values;
}

/** `count` unit faces scattered around basis direction `axis`. */
function mode(axis: number, count: number, seed: number, spread = 0.18): PrototypeFace[] {
  const random = mulberry32(seed);
  const faces: PrototypeFace[] = [];
  for (let index = 0; index < count; index += 1) {
    const values = new Array<number>(DIMENSIONS).fill(0);
    values[axis] = 1;
    for (let d = 0; d < DIMENSIONS; d += 1) values[d] += (random() - 0.5) * spread;
    faces.push({ embedding: unit(values) });
  }
  return faces;
}

/** The shipped number: average linkage between two weighted means of faces. */
function singleCentroid(faces: readonly PrototypeFace[]): number[] {
  const centroid = new Array<number>(DIMENSIONS).fill(0);
  let weight = 0;
  for (const face of faces) {
    const contribution = face.weight ?? 1;
    weight += contribution;
    for (let d = 0; d < DIMENSIONS; d += 1) centroid[d] += face.embedding[d] * contribution;
  }
  return centroid.map((value) => value / weight);
}

function centroidLinkage(a: readonly PrototypeFace[], b: readonly PrototypeFace[]): number {
  const left = singleCentroid(a);
  const right = singleCentroid(b);
  return scaledSimilarity(left, comparisonInverse(left), right, comparisonInverse(right));
}

// --- 1. Shadow. Nothing may be grouping differently because this file exists. -

assert(
  MULTI_PROTOTYPE_ENABLED === false,
  "the multi-prototype flag must ship OFF -- the measurement has not justified turning it on",
);

// --- 2. k = 1 reproduces the shipped centroid EXACTLY. -----------------------
//
// Not "closely". `face-calibration.ts` derives every bar from average linkage,
// and `prototypeLinkage` is only allowed to inherit those bars if its k=1 case
// is the identical quantity. A rescaling here would look like a merge gain in
// the offline measurement and would be nothing of the kind.

{
  const tightA = mode(0, 12, 101);
  const tightB = mode(0, 12, 202);
  const one = derivePrototypes(tightA, { coherenceBar: -1 });
  const two = derivePrototypes(tightB, { coherenceBar: -1 });
  assert(one.length === 1 && two.length === 1, "coherenceBar -1 must never split");
  const viaPrototypes = prototypeLinkage(one, two);
  const viaCentroids = centroidLinkage(tightA, tightB);
  assert(
    Math.abs(viaPrototypes - viaCentroids) < 1e-12,
    `k=1 linkage must equal the shipped centroid linkage exactly (${viaPrototypes} vs ${viaCentroids})`,
  );
  // Vacuity guard. If `centroidLinkage` and `prototypeLinkage` were both
  // returning some constant the equality above would hold and mean nothing.
  assert(
    viaCentroids > 0.5 && viaCentroids < 1,
    "the two tight clusters must actually score a real similarity, not 0 or 1",
  );
  // Sabotage. Move one component of one centroid and the equality must break,
  // which is what shows the comparison is reading the vectors at all.
  const nudged = one.map((prototype) => ({
    ...prototype,
    centroid: prototype.centroid.map((value, index) => (index === 0 ? value * 0.5 : value)),
  }));
  assert(
    Math.abs(prototypeLinkage(nudged, two) - viaCentroids) > 1e-6,
    "SABOTAGE: perturbing a centroid must change the linkage, or the equality above is vacuous",
  );
}

// --- 3. A tile that really does hold two appearances splits. -----------------

const twoAppearances = [...mode(0, 12, 303), ...mode(1, 12, 404)];

{
  // Above the mixed tile's own coherence (two orthogonal modes average near
  // 0.5), so the rule has a reason to look for a second centre.
  const split = derivePrototypes(twoAppearances, { coherenceBar: 0.7 });
  assert(
    split.length === 2,
    `a tile holding two orthogonal appearances must split in two, got ${split.length}`,
  );
  assert(
    split.every((prototype) => prototype.faceCount === 12),
    "the split must recover the two appearances, not shave off a stray face",
  );
  assert(
    split.every((prototype) => prototype.coherence > 0.9),
    "each recovered appearance must be far tighter than the tile it came from",
  );

  // Sabotage 1. The same faces under a bar BELOW the tile's own coherence must
  // stay whole. Without this, "it split" is indistinguishable from "it always
  // splits", which is the failure mode this repo has shipped before.
  const whole = derivePrototypes(twoAppearances, { coherenceBar: 0.3 });
  assert(
    whole.length === 1,
    `the same faces under a bar they already clear must stay one prototype, got ${whole.length}`,
  );

  // Sabotage 2. A tile that is genuinely ONE appearance must stay whole even
  // under the aggressive bar that split the mixed one.
  const oneAppearance = derivePrototypes(mode(0, 24, 505), { coherenceBar: 0.7 });
  assert(
    oneAppearance.length === 1,
    `a single-mode tile must not be carved up at a bar of 0.7, got ${oneAppearance.length}`,
  );
  // ...and it is not that this fixture is unsplittable: raise the bar past its
  // coherence and it does split, so the line above is the bar's doing.
  assert(
    derivePrototypes(mode(0, 24, 505), { coherenceBar: 1 }).length > 1,
    "SABOTAGE: the single-mode tile must be splittable at all, or sabotage 2 is vacuous",
  );
}

// --- 4. The mechanism: a max over sub-centres can only RAISE a score. --------
//
// This is the safety statement for the whole module. Multi-prototype cannot
// make two clusters look less alike, so it can only ADD merges and ADD fusions,
// never remove either -- which is why the offline measurement compares the two
// at equal impostor counts rather than at one shared bar.

{
  const nearbyMode = mode(0, 12, 606);
  const mixed = derivePrototypes(twoAppearances, { coherenceBar: 0.7 });
  const single = derivePrototypes(nearbyMode, { coherenceBar: -1 });
  const multiScore = prototypeLinkage(mixed, single);
  const singleScore = centroidLinkage(twoAppearances, nearbyMode);
  assert(
    multiScore > singleScore,
    `splitting a two-appearance tile must raise its match to one of those appearances (${multiScore} vs ${singleScore})`,
  );
  // The size of the effect is the point: the single mean sits between two
  // orthogonal appearances and matches neither.
  assert(
    singleScore < 0.6 && multiScore > 0.9,
    `the mean must match neither appearance (${singleScore.toFixed(3)}) while a sub-centre matches one (${multiScore.toFixed(3)})`,
  );
  // It must be the MAX, not whichever sub-centre happens to be listed first.
  // Reversing the prototype order is the cheapest way to say that: a max is
  // order-free, and every other reduction over the k*k pairs is not. Without
  // this, `prototypeLinkage` returning its first pair passes every other
  // assertion in this file -- measured, by making exactly that change.
  const reversed = [...mixed].reverse();
  assert(
    Math.abs(prototypeLinkage(reversed, single) - multiScore) < 1e-12,
    "linkage must not depend on the order the prototypes are listed in",
  );
  assert(
    Math.abs(prototypeLinkage(single, mixed) - multiScore) < 1e-12,
    "linkage must not depend on which identity is passed first",
  );
  // ...and the two orderings must genuinely disagree about their FIRST pair, or
  // the invariance above is satisfied by a coincidence rather than by the max.
  const firstPairEach = [mixed[0], reversed[0]].map((prototype) =>
    prototypeLinkage([prototype], single),
  );
  assert(
    Math.abs(firstPairEach[0] - firstPairEach[1]) > 0.5,
    `SABOTAGE: the two sub-centres must score very differently against the same identity (${firstPairEach.map((value) => value.toFixed(3)).join(" vs ")}), or order-invariance is free`,
  );
  assert(
    Math.abs(multiScore - Math.max(...firstPairEach)) < 1e-12,
    "the linkage must be the LARGEST sub-centre pairing, not an average or a first hit",
  );

  // Vacuity guard for the direction: with nobody split, the two are equal, so
  // "multi >= single" is not something that holds by construction of the test.
  const unsplit = derivePrototypes(twoAppearances, { coherenceBar: -1 });
  assert(
    Math.abs(prototypeLinkage(unsplit, single) - singleScore) < 1e-12,
    "SABOTAGE: with no split the two statistics must coincide, not merely order",
  );
}

// --- 5. k is adaptive in BOTH directions, and capped by evidence. ------------

{
  assert(maxPrototypesFor(1) === 1, "a one-face person cannot have appearances");
  assert(maxPrototypesFor(3) === 1, "below the evidence minimum, one prototype");
  assert(maxPrototypesFor(6) === 2 && maxPrototypesFor(20) === 4 && maxPrototypesFor(400) === 6,
    "the size tiers must be 1/2/4/6");

  // Three appearances in a large tile: k follows the faces, not the cap.
  const threeAppearances = [...mode(0, 12, 707), ...mode(1, 12, 808), ...mode(2, 12, 909)];
  const three = derivePrototypes(threeAppearances, { coherenceBar: 0.7 });
  assert(three.length === 3, `three appearances must give three prototypes, got ${three.length}`);
  assert(
    three.every((prototype) => prototype.faceCount === 12),
    "each of the three appearances must come back whole",
  );

  // The cap binds where the evidence is thin: the same three appearances at
  // three faces each are only 9 faces, whose tier allows 2.
  const thin = [...mode(0, 3, 707), ...mode(1, 3, 808), ...mode(2, 3, 909)];
  assert(maxPrototypesFor(thin.length) === 2, "a nine-face tile is allowed two prototypes");
  assert(
    derivePrototypes(thin, { coherenceBar: 1 }).length <= 2,
    "the size tier must cap k even when the faces would support more",
  );
  // Sabotage: it is the CAP doing that, not an inability to find three modes --
  // the same three modes with more faces each got three above.
  assert(three.length === 3, "SABOTAGE: the uncapped case must still reach three");
}

// --- 6. A sub-centre must be evidence, not one stray frame. ------------------

{
  // Eleven faces of one appearance plus a single outlier. The outlier is the
  // loosest thing in the tile and the split would love to isolate it, but a
  // one-face prototype is a raw embedding wearing a cluster's authority, and the
  // max-over-pairs statistic would hand the whole merge decision to it.
  const oneOutlier = [...mode(0, 11, 1111), ...mode(3, 1, 2222)];
  const refused = derivePrototypes(oneOutlier, { coherenceBar: 1 });
  assert(
    refused.every((prototype) => prototype.faceCount >= PROTOTYPE_MIN_FACES),
    `no prototype may hold fewer than ${PROTOTYPE_MIN_FACES} faces, got ${refused.map((p) => p.faceCount).join(",")}`,
  );
  // Sabotage: with TWO outliers the split is allowed, so the refusal above is
  // the evidence floor and not a function that has stopped splitting.
  const twoOutliers = [...mode(0, 11, 1111), ...mode(3, 2, 2222)];
  const allowed = derivePrototypes(twoOutliers, { coherenceBar: 1 });
  assert(
    allowed.some((prototype) => prototype.faceCount === PROTOTYPE_MIN_FACES),
    `two outliers must be allowed to form a sub-centre, got ${allowed.map((p) => p.faceCount).join(",")}`,
  );
  assert(
    refused.length < allowed.length,
    `the one-outlier tile must yield FEWER sub-centres than the two-outlier one (${refused.length} vs ${allowed.length})`,
  );
}

// --- 7. Quality weighting is the clusterer's, not a second opinion. ----------

{
  assert(prototypeWeightFor(true) === 1, "a seedable face votes in full");
  assert(
    prototypeWeightFor(false) === ASSIGNABLE_CENTROID_WEIGHT,
    "an assignable face must vote at exactly the weight face-cluster.ts gives it",
  );
  assert(prototypeWeightFor(undefined) === 1, "an unlabelled face is treated as seedable");

  // A blurry frame from a different direction must move the mean LESS than a
  // sharp one would, because blur is a direction different people share.
  const good = mode(0, 8, 3333);
  const stray = mode(3, 4, 4444);
  const discounted = [...good, ...stray.map((face) => ({ ...face, weight: ASSIGNABLE_CENTROID_WEIGHT }))];
  const full = [...good, ...stray];
  const pulledLess = derivePrototypes(discounted, { coherenceBar: -1 })[0];
  const pulledMore = derivePrototypes(full, { coherenceBar: -1 })[0];
  const anchor = singleCentroid(good);
  const towardsAnchor = (centroid: number[]): number =>
    scaledSimilarity(centroid, comparisonInverse(centroid), anchor, comparisonInverse(anchor));
  assert(
    towardsAnchor(pulledLess.centroid) > towardsAnchor(pulledMore.centroid),
    "discounted faces must move a prototype less than full-weight ones",
  );
  assert(
    Math.abs(pulledLess.weightSum - (8 + 4 * ASSIGNABLE_CENTROID_WEIGHT)) < 1e-12,
    "weightSum must be the sum of quality weights, not a face count",
  );
  // Sabotage: the two centroids must actually differ, or the ordering above is
  // reading floating-point noise.
  assert(
    towardsAnchor(pulledLess.centroid) - towardsAnchor(pulledMore.centroid) > 1e-3,
    "SABOTAGE: the weighting must make a material difference, not a last-bit one",
  );
}

// --- 8. Deterministic. A grouping that moves between runs is unattributable. -

{
  const faces = [...mode(0, 10, 5555), ...mode(1, 10, 6666), ...mode(2, 10, 7777)];
  const first = derivePrototypes(faces, { coherenceBar: 0.7 });
  const second = derivePrototypes(faces, { coherenceBar: 0.7 });
  assert(
    first.length === second.length &&
      first.every(
        (prototype, index) =>
          prototype.faceCount === second[index].faceCount &&
          prototype.centroid.every((value, at) => value === second[index].centroid[at]),
      ),
    "the same faces must give bit-identical prototypes on a second call",
  );
  // Vacuity guard: a function returning [] would satisfy the equality above.
  assert(first.length === 3, "the determinism fixture must actually have produced three prototypes");
}

// --- 9. Degenerate inputs must not produce a prototype at all. ---------------

{
  assert(derivePrototypes([], { coherenceBar: 0.7 }).length === 0, "no faces, no prototypes");
  assert(
    prototypeLinkage([], derivePrototypes(mode(0, 4, 8888), { coherenceBar: -1 })) === 0,
    "linkage against nothing is 0, not -Infinity",
  );
  // Mismatched dimensions are dropped rather than compared, which is what
  // face-cluster.ts does at every comparison site.
  const ragged: PrototypeFace[] = [
    ...mode(0, 4, 9999),
    { embedding: [1, 0, 0] },
  ];
  const built = derivePrototypes(ragged, { coherenceBar: -1 });
  assert(
    built.length === 1 && built[0].faceCount === 4,
    `a face of the wrong width must be ignored, got faceCount ${built[0]?.faceCount}`,
  );
}

console.log("face-prototypes self-check passed");
