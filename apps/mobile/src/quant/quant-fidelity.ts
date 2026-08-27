/**
 * Did quantization break anything?
 *
 * Nothing in this repository could answer that question, which is why no
 * quantized model could be adopted even if one converted cleanly. This module
 * is the gate: it takes two embedding sets computed over the SAME images in the
 * same order -- `reference` from the shipped fp32 model, `candidate` from a
 * quantized one -- and reports whether the candidate would change the product.
 *
 * The metrics are the ones docs/EXPERT-PLAN.md section 8 names, and the reason
 * it names several is that each one alone is fooled:
 *
 *   - Mean cosine hides reordering. Every embedding can move by a hair in the
 *     same direction, keep a mean cosine of 0.999, and still swap the top two
 *     neighbours of every photo. Hence p1/p5 and the neighbour recall.
 *   - Neighbour recall is per-image and local. Two images can each keep their
 *     own neighbour list while the pairs REORDER against each other across the
 *     library, which is what dedupe and diversity actually rank on. Hence the
 *     Spearman of pairwise similarities.
 *   - Spearman is rank-based, so a MONOTONE compression of every similarity
 *     passes it with 1.0 by construction -- while moving every fixed threshold
 *     in the product underneath it. Nothing rank-based can see that, which is
 *     the reason `verificationShift` tests decisions at the real bar instead of
 *     testing ranks. The self-check proves these two are non-redundant.
 *   - Both hide the product. The album is chosen by a diversity pass over these
 *     embeddings, and a change too small to fail either bar can still swap a
 *     photo in the album the owner actually sees. Hence `selectionChange`,
 *     which section 8 says to REPORT rather than require -- an album that
 *     changed is not automatically an album that got worse.
 *
 * Face embeddings are deliberately NOT judged by these bars. See
 * `verificationShift` for why, and for the measurement that settles it.
 *
 * Everything here is pure and works on plain arrays, so the same code runs in
 * the app, in a Node self-check, and in the scratch/quant-fidelity runner over
 * the owner's real library.
 */

export type Embedding = readonly number[];

/** Cosine similarity. Embeddings are NOT assumed to be unit length. */
export function cosine(a: Embedding, b: Embedding): number {
  let dot = 0;
  let aa = 0;
  let bb = 0;
  for (let index = 0; index < a.length; index += 1) {
    dot += a[index] * b[index];
    aa += a[index] * a[index];
    bb += b[index] * b[index];
  }
  const scale = Math.sqrt(aa) * Math.sqrt(bb);
  return scale === 0 ? 0 : dot / scale;
}

/**
 * The value at `fraction` through the sorted sample, nearest-rank.
 *
 * p1 and p5 of an agreement distribution are the photos quantization hurt
 * MOST, which is the number that decides whether a model ships -- a mean is an
 * average over the photos it did not hurt.
 */
export function percentile(values: readonly number[], fraction: number): number {
  if (values.length === 0) return Number.NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.ceil(fraction * sorted.length) - 1;
  return sorted[Math.min(sorted.length - 1, Math.max(0, rank))];
}

export type CosineAgreement = {
  count: number;
  mean: number;
  p1: number;
  p5: number;
  min: number;
};

/**
 * Per-image cosine between the fp32 embedding and the quantized one.
 *
 * This is agreement, not similarity between different photos: row i of
 * `reference` and row i of `candidate` are the SAME image through two models.
 */
export function cosineAgreement(
  reference: readonly Embedding[],
  candidate: readonly Embedding[],
): CosineAgreement {
  if (reference.length !== candidate.length) {
    throw new Error(
      `cosineAgreement needs matched sets: ${reference.length} vs ${candidate.length}`,
    );
  }
  const scores = reference.map((row, index) => cosine(row, candidate[index]));
  return {
    count: scores.length,
    mean: scores.reduce((sum, value) => sum + value, 0) / (scores.length || 1),
    p1: percentile(scores, 0.01),
    p5: percentile(scores, 0.05),
    min: scores.length === 0 ? Number.NaN : Math.min(...scores),
  };
}

/** Indices of the `k` nearest other rows to `index`, nearest first. */
export function topNeighbours(
  embeddings: readonly Embedding[],
  index: number,
  k: number,
): number[] {
  const scored: Array<[number, number]> = [];
  for (let other = 0; other < embeddings.length; other += 1) {
    if (other === index) continue;
    scored.push([other, cosine(embeddings[index], embeddings[other])]);
  }
  // Ties broken by index so the ordering is total: without this a tie could
  // resolve differently in the two sets and be scored as a recall miss that
  // the model never caused.
  scored.sort((a, b) => (b[1] - a[1]) || (a[0] - b[0]));
  return scored.slice(0, k).map(([other]) => other);
}

export type RecallAtK = { k: number; mean: number; worst: number };

/**
 * Of the fp32 model's k nearest neighbours for each image, how many does the
 * quantized model still return in ITS k nearest?
 *
 * Section 8's initial gate is recall@10 >= 0.98. This is the metric that
 * catches reordering, which is the failure that matters: search, dedupe and
 * diversity all consume neighbour order, not absolute embedding values.
 */
export function neighbourRecall(
  reference: readonly Embedding[],
  candidate: readonly Embedding[],
  k: number,
): RecallAtK {
  if (reference.length !== candidate.length) {
    throw new Error(
      `neighbourRecall needs matched sets: ${reference.length} vs ${candidate.length}`,
    );
  }
  const perImage: number[] = [];
  for (let index = 0; index < reference.length; index += 1) {
    const wanted = topNeighbours(reference, index, k);
    if (wanted.length === 0) continue;
    const got = new Set(topNeighbours(candidate, index, k));
    const kept = wanted.filter((neighbour) => got.has(neighbour)).length;
    perImage.push(kept / wanted.length);
  }
  return {
    k,
    mean: perImage.reduce((sum, value) => sum + value, 0) / (perImage.length || 1),
    worst: perImage.length === 0 ? Number.NaN : Math.min(...perImage),
  };
}

/** Ranks with ties averaged, so equal values cannot bias the correlation. */
function ranks(values: readonly number[]): number[] {
  const order = values.map((value, index) => [value, index] as const);
  order.sort((a, b) => a[0] - b[0]);
  const out = new Array<number>(values.length);
  let start = 0;
  while (start < order.length) {
    let end = start;
    while (end + 1 < order.length && order[end + 1][0] === order[start][0]) end += 1;
    const shared = (start + end) / 2 + 1;
    for (let position = start; position <= end; position += 1) {
      out[order[position][1]] = shared;
    }
    start = end + 1;
  }
  return out;
}

/** Spearman rank correlation. */
export function spearman(a: readonly number[], b: readonly number[]): number {
  if (a.length !== b.length) {
    throw new Error(`spearman needs matched samples: ${a.length} vs ${b.length}`);
  }
  if (a.length < 2) return Number.NaN;
  const ra = ranks(a);
  const rb = ranks(b);
  const mean = (values: number[]): number =>
    values.reduce((sum, value) => sum + value, 0) / values.length;
  const ma = mean(ra);
  const mb = mean(rb);
  let cov = 0;
  let va = 0;
  let vb = 0;
  for (let index = 0; index < ra.length; index += 1) {
    const da = ra[index] - ma;
    const db = rb[index] - mb;
    cov += da * db;
    va += da * da;
    vb += db * db;
  }
  const scale = Math.sqrt(va) * Math.sqrt(vb);
  return scale === 0 ? Number.NaN : cov / scale;
}

/**
 * Spearman of the pairwise similarity values over the same sampled pairs.
 *
 * Neighbour recall asks each image about its OWN top-k. This asks whether the
 * whole library still ranks its pairs the same way, which is the ordering
 * dedupe and diversity consume.
 *
 * It cannot see a monotone rescaling -- that is what `verificationShift` is
 * for. Reporting a rank statistic as if it guarded a threshold is the mistake
 * this comment exists to prevent.
 */
export function pairSimilaritySpearman(
  reference: readonly Embedding[],
  candidate: readonly Embedding[],
  pairs: ReadonlyArray<readonly [number, number]>,
): number {
  const a = pairs.map(([i, j]) => cosine(reference[i], reference[j]));
  const b = pairs.map(([i, j]) => cosine(candidate[i], candidate[j]));
  return spearman(a, b);
}

/**
 * The diversity pass, reduced to the part quantization can move.
 *
 * Album selection is farthest-point over the embeddings: take a seed, then
 * repeatedly take whichever candidate is least like everything already taken.
 * It is the greedy step that makes this worth testing -- one flipped
 * comparison early changes every later pick, so a drift far too small to fail
 * a cosine bar can still hand the owner a different album.
 */
export function diverseSelection(
  embeddings: readonly Embedding[],
  count: number,
  seed = 0,
): number[] {
  if (embeddings.length === 0 || count <= 0) return [];
  const chosen = [seed];
  const best = embeddings.map((row) => cosine(row, embeddings[seed]));
  while (chosen.length < Math.min(count, embeddings.length)) {
    let pick = -1;
    let pickScore = Number.POSITIVE_INFINITY;
    for (let index = 0; index < embeddings.length; index += 1) {
      if (chosen.includes(index)) continue;
      // Ties broken by index, for the same reason as in topNeighbours.
      if (best[index] < pickScore) {
        pickScore = best[index];
        pick = index;
      }
    }
    if (pick < 0) break;
    chosen.push(pick);
    for (let index = 0; index < embeddings.length; index += 1) {
      const score = cosine(embeddings[index], embeddings[pick]);
      if (score > best[index]) best[index] = score;
    }
  }
  return chosen;
}

export type SelectionChange = {
  size: number;
  kept: number;
  changed: number;
  /** Fraction of the album that is a different photo. */
  rate: number;
};

/** How much of the album the owner would see actually changes. */
export function selectionChange(
  reference: readonly Embedding[],
  candidate: readonly Embedding[],
  count: number,
  seed = 0,
): SelectionChange {
  const before = new Set(diverseSelection(reference, count, seed));
  const after = diverseSelection(candidate, count, seed);
  const kept = after.filter((index) => before.has(index)).length;
  const size = after.length;
  return { size, kept, changed: size - kept, rate: size === 0 ? 0 : (size - kept) / size };
}

export type VerificationShift = {
  threshold: number;
  genuinePairs: number;
  impostorPairs: number;
  /** Same-person pairs accepted, before and after. Higher is better. */
  genuineAcceptBefore: number;
  genuineAcceptAfter: number;
  /** Different-person pairs accepted, before and after. LOWER is better. */
  impostorAcceptBefore: number;
  impostorAcceptAfter: number;
  /** Pairs whose accept/reject answer changed at all, either direction. */
  flipRate: number;
  /** New impostor accepts minus repaired ones, as a fraction of impostor pairs. */
  impostorAcceptDelta: number;
};

/**
 * Face quantization judged on the only thing that matters: does this pair of
 * faces still get the same answer?
 *
 * Section 8 says face embedding quantization is the sensitive case, and cosine
 * agreement is the wrong instrument for it. A face model can hold a mean
 * agreement of 0.99 with fp32 and still be unusable, because the decision the
 * product makes is a THRESHOLD on a similarity between two DIFFERENT faces, and
 * the pairs that decide identity sit within a few hundredths of that bar.
 *
 * And they sit there because of who is in the library. Relatives look alike,
 * and an infant at one month and at one year is barely the same face. LFW does
 * not contain that population, so an int8 face model that passes LFW says
 * nothing about this library. Any candidate must be measured HERE, on family
 * pairs, which is what scratch/quant-fidelity/measure.ts does.
 *
 * How bad it is depends on which pair you mean, and the two readings differ by
 * 4x on the owner's library -- measured, because the repository carried the
 * first figure as prose with no construction attached:
 *
 *   face vs another person's CENTROID   3.95%  above 0.20  (the recorded "4.1%")
 *   face vs FACE in the same photo     16.79%  above 0.20
 *
 * A centroid is an average over many faces and so is quieter than any single
 * face. Quantization is judged against the second, harsher number, because two
 * faces in one photo share illumination, sensor and moment, and in a family
 * library they are frequently relatives.
 *
 * `genuine` and `impostor` are index pairs into both sets. Same-photo pairs are
 * free impostor labels: clustering already cannot-links two faces found in one
 * photo, so they are known to be different people without anyone labelling
 * anything.
 */
export function verificationShift(
  reference: readonly Embedding[],
  candidate: readonly Embedding[],
  genuine: ReadonlyArray<readonly [number, number]>,
  impostor: ReadonlyArray<readonly [number, number]>,
  threshold: number,
): VerificationShift {
  const accepts = (
    set: readonly Embedding[],
    pairs: ReadonlyArray<readonly [number, number]>,
  ): boolean[] => pairs.map(([i, j]) => cosine(set[i], set[j]) >= threshold);

  const genuineBefore = accepts(reference, genuine);
  const genuineAfter = accepts(candidate, genuine);
  const impostorBefore = accepts(reference, impostor);
  const impostorAfter = accepts(candidate, impostor);

  const rate = (flags: boolean[]): number =>
    flags.length === 0 ? Number.NaN : flags.filter(Boolean).length / flags.length;
  const flips = (before: boolean[], after: boolean[]): number =>
    before.filter((value, index) => value !== after[index]).length;

  const total = genuine.length + impostor.length;
  return {
    threshold,
    genuinePairs: genuine.length,
    impostorPairs: impostor.length,
    genuineAcceptBefore: rate(genuineBefore),
    genuineAcceptAfter: rate(genuineAfter),
    impostorAcceptBefore: rate(impostorBefore),
    impostorAcceptAfter: rate(impostorAfter),
    flipRate:
      total === 0
        ? Number.NaN
        : (flips(genuineBefore, genuineAfter) + flips(impostorBefore, impostorAfter)) / total,
    impostorAcceptDelta:
      impostor.length === 0
        ? Number.NaN
        : rate(impostorAfter) - rate(impostorBefore),
  };
}

export type FidelityReport = {
  agreement: CosineAgreement;
  recall: RecallAtK;
  pairSpearman: number;
  selection: SelectionChange;
  /** Section 8's stated bar, and whether this candidate clears it. */
  passed: boolean;
  failures: string[];
};

/**
 * The bars from docs/EXPERT-PLAN.md section 8.
 *
 * Only recall@10 >= 0.98 is written there as a number, and it is written as an
 * INITIAL gate "to be validated". The cosine floors below are not from the
 * plan; they are here so a candidate that passes recall by luck on a small
 * sample still has to show its worst photos are sane. `selectionChange` is
 * reported and never gates, because section 8 says album overlap is reported,
 * not required identical.
 */
export const FIDELITY_BARS = {
  recallAt10: 0.98,
  meanCosine: 0.99,
  p1Cosine: 0.95,
  pairSpearman: 0.99,
} as const;

export function fidelityReport(
  reference: readonly Embedding[],
  candidate: readonly Embedding[],
  options: { albumSize?: number; pairs?: ReadonlyArray<readonly [number, number]> } = {},
): FidelityReport {
  const agreement = cosineAgreement(reference, candidate);
  const recall = neighbourRecall(reference, candidate, 10);
  const pairs =
    options.pairs ??
    reference.flatMap((_, i) =>
      reference.slice(i + 1).map((__, offset) => [i, i + 1 + offset] as const),
    );
  const pairSpearmanValue = pairSimilaritySpearman(reference, candidate, pairs);
  const selection = selectionChange(reference, candidate, options.albumSize ?? 10);

  const failures: string[] = [];
  if (!(recall.mean >= FIDELITY_BARS.recallAt10)) {
    failures.push(
      `recall@10 ${recall.mean.toFixed(4)} < ${FIDELITY_BARS.recallAt10} -- neighbours reordered`,
    );
  }
  if (!(agreement.mean >= FIDELITY_BARS.meanCosine)) {
    failures.push(`mean cosine ${agreement.mean.toFixed(4)} < ${FIDELITY_BARS.meanCosine}`);
  }
  if (!(agreement.p1 >= FIDELITY_BARS.p1Cosine)) {
    failures.push(
      `p1 cosine ${agreement.p1.toFixed(4)} < ${FIDELITY_BARS.p1Cosine} -- the worst photos moved`,
    );
  }
  if (!(pairSpearmanValue >= FIDELITY_BARS.pairSpearman)) {
    failures.push(
      `pairwise Spearman ${pairSpearmanValue.toFixed(4)} < ${FIDELITY_BARS.pairSpearman} -- similarity scale moved under the fixed thresholds`,
    );
  }
  return {
    agreement,
    recall,
    pairSpearman: pairSpearmanValue,
    selection,
    passed: failures.length === 0,
    failures,
  };
}
