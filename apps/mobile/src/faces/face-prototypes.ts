/**
 * SHADOW ONLY. Multi-prototype identity representation, measured against the
 * shipped single-centroid rule and wired into nothing.
 *
 * Nothing in this file is imported by `face-cluster.ts`, `face-index.ts` or any
 * screen, and `MULTI_PROTOTYPE_ENABLED` is the flag that would have to flip
 * before it could be. It exists so the question below can be answered with
 * numbers instead of an argument.
 *
 * THE QUESTION. A person is stored as ONE weighted mean, and the owner's
 * library is 2,244 tiles for a small cast. The suspicion is structural rather
 * than a badly-chosen bar: the library spans an infant's first two years, so a
 * single mean sits between a three-month-old and a fourteen-month-old and
 * matches neither. Splitting each identity into k sub-centres — sub-centre
 * ArcFace's idea — should let the three-month half of one tile meet the
 * three-month half of the other, at a similarity the means never reach.
 *
 * WHY IT REDUCES EXACTLY TO TODAY AT k = 1, and why that matters. A prototype
 * is a weighted mean of UNIT face embeddings, never renormalized, and
 * `prototypeLinkage` scores a pair with the same `scaledSimilarity`
 * `face-cluster.ts` uses. For unit members that value is the mean cosine over
 * every cross pair — average linkage — so with one prototype per side it is
 * bit-for-bit the number the merge sweep already computes, and every calibrated
 * bar in `face-calibration.ts` keeps its meaning. Any gain measured here is
 * therefore the representation's, not a rescaling's.
 * `face-prototypes.test.ts` asserts that identity directly.
 *
 * WHAT IT DOES TO SAFETY, stated up front because it is the whole risk. The
 * statistic is a MAX over sub-centre pairs, and a max over k*k draws sits above
 * the mean of the same draws for two DIFFERENT people just as surely as for two
 * halves of one. It cannot be compared to today's number at a fixed bar; it can
 * only be compared on the curve of merges gained against impostors admitted,
 * with the impostors labelled by co-occurrence. That measurement is
 * `scratch/multi-prototype/measure.ts`, and it is the deliverable — this module
 * is only what it measures.
 *
 * THE MEASURED ANSWER, on the owner's 17,768-face library: it does NOT beat the
 * single centroid, and the flag stays false. Three numbers, with the full
 * tables in the harness header:
 *
 *   - Of the 937 people holding 2+ faces, ZERO sit below the library's own
 *     calibrated assignment bar of 0.448 on mean intra-tile cosine (p05 0.477,
 *     median 0.625), and the eight largest tiles — 1,014 faces down to 260 —
 *     sit at 0.583 to 0.664. The tiles are already single-appearance. The
 *     infant's drift shows up BETWEEN tiles, never inside one, so the second
 *     prototype has nothing to find: at the library's measured bar this file
 *     splits 21 of 2,248 people.
 *   - Cutting each person into the same k pieces of the same sizes AT RANDOM
 *     gains the same merges at the same impostor count (162 against 161 at a
 *     60-impostor budget). Whatever the split buys is the max, not the
 *     appearance modelling.
 *   - At impostor budgets of 0, 2, 4 and 8, every policy measured — including
 *     the shipped centroid — gains exactly ZERO merges.
 *
 * The file is kept rather than deleted because the negative result is the
 * valuable part and it has to stay reproducible: the harness imports this, and
 * a future model, a re-scan with real capture times, or a library whose tiles
 * are genuinely broad would be measured by re-running it rather than by
 * re-arguing it. Turning the flag on needs a NEW measurement, not this one.
 */

// @ts-expect-error TypeScript bundler resolution normally omits source extensions.
import { ASSIGNABLE_CENTROID_WEIGHT, comparisonInverse, scaledSimilarity } from "./face-cluster.ts";

/**
 * The shadow flag. False, and nothing reads it yet.
 *
 * Deliberately a plain constant rather than a setting: a runtime toggle for a
 * grouping rule is a way to ship two clusterers and find out which one a user
 * is on from a bug report. When the measurement justifies the change, this
 * becomes a decision made once in the code, and the persisted index carries a
 * `calibration` tag the way the average-linkage switch already does.
 */
export const MULTI_PROTOTYPE_ENABLED = false;

/** One face's contribution: a unit embedding and its quality weight. */
export type PrototypeFace = {
  embedding: readonly number[];
  /** Defaults to the seedable weight; `seedable: false` faces pass 0.3. */
  weight?: number;
};

export type Prototype = {
  /** Weighted mean of member unit embeddings. NOT renormalized — see above. */
  centroid: number[];
  /** 1/max(1,|centroid|), so `prototypeLinkage` never recomputes a norm. */
  inverse: number;
  faceCount: number;
  weightSum: number;
  /**
   * Mean weighted cosine between DISTINCT members, in [-1, 1]. 1 for a single
   * face, which is honest: one face agrees with itself perfectly and carries no
   * evidence about spread either way.
   */
  coherence: number;
};

/**
 * Ceiling on prototypes by cluster size: 1 / 2 / 4 / 6.
 *
 * A cap, not a target — `derivePrototypes` stops early whenever the faces stop
 * asking for another sub-centre. The tiers exist because k prototypes over n
 * faces is a claim about n/k faces of evidence per mode, and a mode supported
 * by one bad frame is precisely the thing that fuses two people.
 *
 * The first boundary is `MERGE_EVIDENCE_MIN_FACES`: below the point where this
 * codebase already refuses to trust a cluster average, subdividing that average
 * cannot help.
 */
export const PROTOTYPE_SIZE_TIERS: ReadonlyArray<{ minFaces: number; max: number }> = [
  { minFaces: 40, max: 6 },
  { minFaces: 12, max: 4 },
  { minFaces: 4, max: 2 },
  { minFaces: 0, max: 1 },
];

/**
 * A sub-centre must hold at least this many faces and this much quality weight.
 *
 * Two faces because a one-face prototype is a raw embedding wearing a cluster's
 * authority, and the max-over-pairs statistic would hand the whole merge
 * decision to the single worst frame in the library. One full unit of weight
 * because `ASSIGNABLE_CENTROID_WEIGHT` already says a blurry face is worth 0.3
 * of a vote: three blurry faces are not a mode, they are what blur looks like,
 * and blur is a direction two DIFFERENT people share.
 */
export const PROTOTYPE_MIN_FACES = 2;
export const PROTOTYPE_MIN_WEIGHT = 1;

/** Lloyd iterations per split. Assignments settle in two or three in practice. */
const SPLIT_ITERATIONS = 8;

export function maxPrototypesFor(faceCount: number): number {
  for (const tier of PROTOTYPE_SIZE_TIERS) {
    if (faceCount >= tier.minFaces) return tier.max;
  }
  return 1;
}

function dot(a: readonly number[], b: readonly number[]): number {
  let total = 0;
  for (let index = 0; index < a.length; index += 1) {
    total += a[index] * b[index];
  }
  return total;
}

function weightOf(face: PrototypeFace): number {
  const weight = face.weight;
  return typeof weight === "number" && Number.isFinite(weight) && weight > 0
    ? weight
    : 1;
}

/**
 * Builds one prototype from a member list.
 *
 * `coherence` comes out of the centroid's own length rather than an O(n^2) pass
 * over the members. For unit members,
 *
 *   |c|^2 * W^2 = sum_i w_i^2 + sum_{i != j} w_i w_j cos(v_i, v_j)
 *
 * so the mean off-diagonal cosine is (|c|^2 W^2 - S2) / (W^2 - S2), where
 * S2 = sum of squared weights. Exact, not an approximation, and it is the same
 * identity `centroidScale` in face-cluster.ts rests on.
 */
function buildPrototype(
  faces: readonly PrototypeFace[],
  members: readonly number[],
  dimensions: number,
): Prototype {
  const centroid = new Array<number>(dimensions).fill(0);
  let weightSum = 0;
  let squaredWeights = 0;
  for (const memberIndex of members) {
    const face = faces[memberIndex];
    const weight = weightOf(face);
    weightSum += weight;
    squaredWeights += weight * weight;
    const embedding = face.embedding;
    for (let index = 0; index < dimensions; index += 1) {
      centroid[index] += embedding[index] * weight;
    }
  }
  if (weightSum > 0) {
    for (let index = 0; index < dimensions; index += 1) {
      centroid[index] /= weightSum;
    }
  }
  const spread = weightSum * weightSum - squaredWeights;
  const coherence =
    spread > Number.EPSILON
      ? Math.max(
          -1,
          Math.min(1, (dot(centroid, centroid) * weightSum * weightSum - squaredWeights) / spread),
        )
      : 1;
  return {
    centroid,
    inverse: comparisonInverse(centroid),
    faceCount: members.length,
    weightSum,
    coherence,
  };
}

/**
 * Deterministic weighted spherical 2-means over one prototype's members.
 *
 * Seeded farthest-point rather than randomly, because a random seed makes a
 * grouping that differs between two runs over the same library, and this
 * codebase already renumbers people on every rebuild — a second source of churn
 * would make any regression unattributable. The first seed is the member
 * furthest from the parent centroid (the mode the mean is failing hardest), the
 * second is the member furthest from the first. Ties fall to the lower index,
 * so the answer depends only on the member order handed in.
 *
 * Returns undefined when the split degenerates: a side lost every member, or a
 * side is too thin to be evidence under `PROTOTYPE_MIN_FACES` /
 * `PROTOTYPE_MIN_WEIGHT`.
 */
function splitMembers(
  faces: readonly PrototypeFace[],
  members: readonly number[],
  dimensions: number,
): [number[], number[]] | undefined {
  if (members.length < PROTOTYPE_MIN_FACES * 2) return undefined;
  const parent = buildPrototype(faces, members, dimensions);
  let firstSeed = members[0];
  let worst = Number.POSITIVE_INFINITY;
  for (const memberIndex of members) {
    const similarity = dot(faces[memberIndex].embedding, parent.centroid);
    if (similarity < worst) {
      worst = similarity;
      firstSeed = memberIndex;
    }
  }
  let secondSeed = members[0] === firstSeed ? members[1] : members[0];
  worst = Number.POSITIVE_INFINITY;
  for (const memberIndex of members) {
    if (memberIndex === firstSeed) continue;
    const similarity = dot(faces[memberIndex].embedding, faces[firstSeed].embedding);
    if (similarity < worst) {
      worst = similarity;
      secondSeed = memberIndex;
    }
  }

  let left = faces[firstSeed].embedding as readonly number[];
  let right = faces[secondSeed].embedding as readonly number[];
  let leftMembers: number[] = [];
  let rightMembers: number[] = [];
  for (let round = 0; round < SPLIT_ITERATIONS; round += 1) {
    const nextLeft: number[] = [];
    const nextRight: number[] = [];
    for (const memberIndex of members) {
      const embedding = faces[memberIndex].embedding;
      // Raw dot against an UNNORMALIZED centre, deliberately: for unit members
      // that is the mean cosine to everything already on that side — the same
      // average-linkage criterion `face-cluster.ts` assigns a face to a person
      // with. Normalizing (textbook spherical k-means) would score a loose side
      // higher than a tight one, which is the runaway `centroidScale` documents.
      //
      // Ties go left, which only matters for a member exactly between the two
      // centres and keeps the outcome order-determined rather than arbitrary.
      if (dot(embedding, left) >= dot(embedding, right)) nextLeft.push(memberIndex);
      else nextRight.push(memberIndex);
    }
    if (nextLeft.length === 0 || nextRight.length === 0) return undefined;
    const settled =
      nextLeft.length === leftMembers.length &&
      nextLeft.every((memberIndex, at) => memberIndex === leftMembers[at]);
    leftMembers = nextLeft;
    rightMembers = nextRight;
    if (settled) break;
    left = buildPrototype(faces, leftMembers, dimensions).centroid;
    right = buildPrototype(faces, rightMembers, dimensions).centroid;
  }

  for (const side of [leftMembers, rightMembers]) {
    if (side.length < PROTOTYPE_MIN_FACES) return undefined;
    let weight = 0;
    for (const memberIndex of side) weight += weightOf(faces[memberIndex]);
    if (weight < PROTOTYPE_MIN_WEIGHT) return undefined;
  }
  return [leftMembers, rightMembers];
}

export type PrototypeOptions = {
  /**
   * The coherence a sub-centre must reach before it stops being subdivided.
   *
   * There is no right constant for this and one must not be invented: pass the
   * library's OWN calibrated assignment bar (`face-calibration.ts`), which is
   * the similarity at which this library's different-person pairs run out. A
   * group whose members agree less than that is not demonstrably one appearance
   * by the library's own standard, so it is exactly the group worth splitting.
   */
  coherenceBar: number;
  /** Overrides the size tier. For tests and sweeps only. */
  maxPrototypes?: number;
};

/**
 * k prototypes for one identity, k chosen by the faces rather than by a config.
 *
 * Bisecting: start from the single mean this codebase already stores, then
 * repeatedly take the LOOSEST prototype still under `coherenceBar` and try to
 * split it. Two independent stopping rules, and it is normal for the first to
 * fire long before the second:
 *
 *   - the loosest prototype has reached the bar (the faces agree; there is no
 *     second appearance to find)
 *   - the split degenerates or its halves are too thin to be evidence
 *
 * so k is adaptive in both directions — a 500-face tile of one tightly-shot
 * adult comes back with ONE prototype, and a 40-face tile spanning an infant's
 * first year comes back with several. A cap by size tier bounds the worst case.
 *
 * Deterministic: same faces in the same order give the same prototypes.
 */
export function derivePrototypes(
  faces: readonly PrototypeFace[],
  options: PrototypeOptions,
): Prototype[] {
  const dimensions = faces[0]?.embedding.length ?? 0;
  if (dimensions === 0) return [];
  const usable: number[] = [];
  for (let index = 0; index < faces.length; index += 1) {
    if (faces[index].embedding.length === dimensions) usable.push(index);
  }
  if (usable.length === 0) return [];

  const cap = Math.max(
    1,
    Number.isFinite(options.maxPrototypes)
      ? (options.maxPrototypes as number)
      : maxPrototypesFor(usable.length),
  );
  const bar = Number.isFinite(options.coherenceBar) ? options.coherenceBar : 1;

  let groups: Array<{ members: number[]; prototype: Prototype }> = [
    { members: usable, prototype: buildPrototype(faces, usable, dimensions) },
  ];
  // Splits already refused, so a loose-but-unsplittable prototype does not stall
  // the loop by winning "loosest" forever.
  const exhausted = new Set<number>();
  while (groups.length < cap) {
    let target = -1;
    let loosest = Number.POSITIVE_INFINITY;
    for (let index = 0; index < groups.length; index += 1) {
      if (exhausted.has(index)) continue;
      const coherence = groups[index].prototype.coherence;
      if (coherence < bar && coherence < loosest) {
        loosest = coherence;
        target = index;
      }
    }
    if (target === -1) break;
    const split = splitMembers(faces, groups[target].members, dimensions);
    if (!split) {
      exhausted.add(target);
      continue;
    }
    const [leftMembers, rightMembers] = split;
    const left = buildPrototype(faces, leftMembers, dimensions);
    const right = buildPrototype(faces, rightMembers, dimensions);
    // A split that tightens nothing is churn: it halves the evidence behind two
    // sub-centres and buys no new appearance. Refuse it and stop working on
    // this group.
    if (Math.max(left.coherence, right.coherence) <= groups[target].prototype.coherence) {
      exhausted.add(target);
      continue;
    }
    const rest = groups.filter((_group, index) => index !== target);
    groups = [
      ...rest,
      { members: leftMembers, prototype: left },
      { members: rightMembers, prototype: right },
    ];
    // Indices shifted; the refusals were about groups that no longer exist at
    // those positions, and re-deriving them costs one wasted split attempt each.
    exhausted.clear();
  }

  return groups.map((group) => group.prototype);
}

/**
 * Best sub-centre-to-sub-centre average linkage between two identities.
 *
 * A MAX, and the reason is the case the whole module exists for: one tile holds
 * an infant's third month and another holds the same infant's fourteenth, so
 * every OTHER pairing of their sub-centres is genuinely two different
 * appearances and averaging them in is averaging in the noise the single
 * centroid already drowned in.
 *
 * The cost of that choice is stated in the file header and is not small: a max
 * over k*k draws rises for two different people too, so this number is NOT
 * comparable to today's at today's bar. It is comparable only on the
 * merges-per-impostor curve.
 *
 * With one prototype per side this is exactly `scaledSimilarity` over the two
 * stored centroids — the number the merge sweep computes today.
 */
export function prototypeLinkage(
  a: readonly Prototype[],
  b: readonly Prototype[],
): number {
  let best = Number.NEGATIVE_INFINITY;
  for (const left of a) {
    for (const right of b) {
      if (
        left.centroid.length === 0 ||
        left.centroid.length !== right.centroid.length
      ) {
        continue;
      }
      const similarity = scaledSimilarity(
        left.centroid,
        left.inverse,
        right.centroid,
        right.inverse,
      );
      if (similarity > best) best = similarity;
    }
  }
  return best === Number.NEGATIVE_INFINITY ? 0 : best;
}

/**
 * The weight a face contributes, from the same tier the scanner already assigns.
 *
 * Here rather than at each call site so a shadow measurement cannot quietly
 * weight faces differently than `face-cluster.ts` does, which would make its
 * prototypes disagree with the centroids it is being compared against for a
 * reason that has nothing to do with prototypes.
 */
export function prototypeWeightFor(seedable: boolean | undefined): number {
  return seedable === false ? ASSIGNABLE_CENTROID_WEIGHT : 1;
}
