/**
 * The album objective from EXPERT-PLAN section 15 ("Album selection objective"),
 * as a pure numeric module with no knowledge of photos, React Native or models.
 *
 *   F(S) = Q(S) + λf·FL(S) + Σ_c λc·Cc(S)
 *
 *   Q(S)  = Σ_{i∈S} q_i                                    modular
 *   FL(S) = Σ_u a_u·max_{i∈S} sim(u,i) / Σ_u a_u           facility location
 *   Cc(S) = Σ_{g∈c} ω_g·h(count_g(S)),  h(n) = 1 − e^(−τn) saturating coverage
 *
 * Why this shape and not a DPP. A DPP needs an L-kernel that is positive
 * semi-definite, and the similarity this product actually has is a hand-blended
 * mixture of CLIP cosine, face-set overlap, pose agreement, place equality and
 * a time decay. Nothing makes that mixture PSD: place-equality alone is a block
 * indicator matrix, which IS PSD, but adding a time kernel and a set-overlap
 * kernel with independent weights is only PSD because each part happens to be —
 * and the pose/place parts are only PSD while they stay exact indicators. One
 * "similar place" softening (0.5 for the same city, 1.0 for the same room)
 * breaks it, and a non-PSD L makes log det(L_S) undefined for some S, so the
 * maximizer silently optimizes garbage. Facility location needs only
 * `sim ≥ 0` — a property this module CHECKS — and buys the same repulsion:
 * once one member of a burst is in S, every other member's marginal FL gain is
 * max(0, sim − best) ≈ 0.
 *
 * Every term is monotone (adding a photo never lowers F) and submodular
 * (marginal gains never grow as S grows):
 *   - Q is modular, so trivially both.
 *   - FL: max(0, sim(u,i) − best_u) can only shrink as best_u rises.
 *   - Cc: h is concave and counts only increase.
 * Monotone + submodular + non-negative is exactly the precondition that makes
 * the stale-bound skip in `lazyGreedy` EXACT rather than an approximation, and
 * it is what earns the (1 − 1/e) bound under a cardinality constraint.
 */

/** τ. ln 2 makes h's marginal 0.5^(n+1) — the planner's own bucket decay. */
export const COVERAGE_SATURATION = Math.LN2;

export type CoverageCategory = {
  /** λc — what one unit of this kind of variety is worth. */
  weight: number;
  /** ω_g — per-group importance, indexed by group. */
  groupWeight: readonly number[];
  /** Group indices each item belongs to; an item may cover several people. */
  membership: readonly (readonly number[])[];
};

export type SubmodularProblem = {
  /** Modular per-photo term q_i, with its weights already folded in. */
  quality: readonly number[];
  /** n×n blend in [0,1]. `similarity[u][i]` is how well i represents u. */
  similarity: readonly (readonly number[])[];
  /** a_u ≥ 0 — the cost of leaving u unrepresented. */
  importance: readonly number[];
  /** λf */
  facilityWeight: number;
  categories: readonly CoverageCategory[];
  /** τ in h(n) = 1 − e^(−τn). */
  saturation: number;
};

export type ObjectiveState = {
  selected: number[];
  /** max_{i∈S} sim(u,i) per u; 0 while S is empty. */
  best: number[];
  /** count_g(S) per category, per group. */
  counts: number[][];
  importanceTotal: number;
};

export type GainBreakdown = {
  total: number;
  quality: number;
  facility: number;
  /** One entry per category, in `problem.categories` order. */
  coverage: number[];
};

export function validateProblem(problem: SubmodularProblem) {
  const size = problem.quality.length;
  if (size === 0) throw new Error("submodular problem is empty");
  if (problem.similarity.length !== size || problem.importance.length !== size) {
    throw new Error("submodular problem arrays disagree on size");
  }
  if (!Number.isFinite(problem.facilityWeight) || problem.facilityWeight < 0) {
    throw new Error("facilityWeight must be finite and non-negative");
  }
  if (!Number.isFinite(problem.saturation) || problem.saturation <= 0) {
    throw new Error("saturation must be finite and positive");
  }
  for (let index = 0; index < size; index += 1) {
    if (!Number.isFinite(problem.quality[index])) {
      throw new Error(`quality[${index}] is not finite`);
    }
    if (!Number.isFinite(problem.importance[index]) || problem.importance[index] < 0) {
      throw new Error(`importance[${index}] must be finite and non-negative`);
    }
    const row = problem.similarity[index];
    if (row.length !== size) throw new Error("similarity must be square");
    for (let other = 0; other < size; other += 1) {
      // The non-negativity that replaces a PSD kernel. A negative similarity
      // would let `max(0, sim − best)` stop being the true FL increment, and
      // the lazy skip would then accept a stale bound that is NOT an upper
      // bound — a silent, non-deterministic wrong answer rather than a crash.
      if (!Number.isFinite(row[other]) || row[other] < 0 || row[other] > 1) {
        throw new Error(`similarity[${index}][${other}] must be finite in [0,1]`);
      }
    }
    if (row[index] !== 1) throw new Error(`similarity[${index}][${index}] must be 1`);
  }
  for (const category of problem.categories) {
    if (!Number.isFinite(category.weight) || category.weight < 0) {
      throw new Error("category weight must be finite and non-negative");
    }
    if (category.membership.length !== size) {
      throw new Error("category membership must cover every item");
    }
    for (const groups of category.membership) {
      for (const group of groups) {
        if (!Number.isInteger(group) || group < 0 || group >= category.groupWeight.length) {
          throw new Error(`category membership references unknown group ${group}`);
        }
      }
    }
    if (category.groupWeight.some((weight) => !Number.isFinite(weight) || weight < 0)) {
      throw new Error("group weight must be finite and non-negative");
    }
  }
}

export function emptyState(problem: SubmodularProblem): ObjectiveState {
  return {
    selected: [],
    best: problem.quality.map(() => 0),
    counts: problem.categories.map((category) => category.groupWeight.map(() => 0)),
    importanceTotal: problem.importance.reduce((sum, value) => sum + value, 0),
  };
}

/** h(n+1) − h(n) = e^(−τn)·(1 − e^(−τ)). */
function coverageStep(saturation: number, count: number) {
  return Math.exp(-saturation * count) * (1 - Math.exp(-saturation));
}

export function gainBreakdown(
  problem: SubmodularProblem,
  state: ObjectiveState,
  index: number,
): GainBreakdown {
  let facility = 0;
  if (problem.facilityWeight > 0 && state.importanceTotal > 0) {
    let sum = 0;
    for (let other = 0; other < problem.importance.length; other += 1) {
      const weight = problem.importance[other];
      if (weight === 0) continue;
      const increment = problem.similarity[other][index] - state.best[other];
      if (increment > 0) sum += weight * increment;
    }
    facility = (problem.facilityWeight * sum) / state.importanceTotal;
  }
  const coverage = problem.categories.map((category, position) => {
    if (category.weight === 0) return 0;
    let sum = 0;
    for (const group of category.membership[index]) {
      sum +=
        category.groupWeight[group] *
        coverageStep(problem.saturation, state.counts[position][group]);
    }
    return category.weight * sum;
  });
  const quality = problem.quality[index];
  const total = coverage.reduce((sum, value) => sum + value, quality + facility);
  return { total: quantize(total), quality, facility, coverage };
}

export function marginalGain(
  problem: SubmodularProblem,
  state: ObjectiveState,
  index: number,
): number {
  return gainBreakdown(problem, state, index).total;
}

export function applyPick(
  problem: SubmodularProblem,
  state: ObjectiveState,
  index: number,
) {
  state.selected.push(index);
  for (let other = 0; other < state.best.length; other += 1) {
    const similarity = problem.similarity[other][index];
    if (similarity > state.best[other]) state.best[other] = similarity;
  }
  problem.categories.forEach((category, position) => {
    for (const group of category.membership[index]) state.counts[position][group] += 1;
  });
}

/** F(S) computed from scratch — the swap pass compares whole sets with it. */
export function objectiveValue(
  problem: SubmodularProblem,
  selected: readonly number[],
): number {
  const state = emptyState(problem);
  let value = 0;
  for (const index of selected) {
    value += marginalGain(problem, state, index);
    applyPick(problem, state, index);
  }
  return quantize(value);
}

// --- Lazy greedy -----------------------------------------------------------

export type GreedyHooks = {
  budget: number;
  /** Every item index, in the order ties must be broken. */
  order: readonly number[];
  /** True marginal gain of `index` against the committed selection. */
  marginal: (index: number) => number;
  /** Hard constraints against the committed selection. */
  blocked: (index: number) => boolean;
  commit: (index: number, gain: number) => void;
  /** Widen the softest cap. False when nothing is left to relax. */
  relax: () => boolean;
};

export type GreedyResult = {
  chosen: number[];
  /** How many true marginal gains were computed. The point of being lazy. */
  evaluations: number;
};

type HeapEntry = { index: number; rank: number; bound: number; stamp: number };

/**
 * Minoux's accelerated greedy. Marginal gains only ever shrink, so a bound
 * computed at an earlier round is still an UPPER bound now: an entry that sits
 * at the top of the heap with a freshly computed gain is the true argmax and
 * every other entry can be left stale.
 *
 * Hard constraints are handled by popping blocked entries aside for the round
 * and putting them back afterwards. That stays exact: the accepted entry's
 * bound dominates every bound still in the heap AND every bound set aside, so
 * it is the argmax over the whole feasible set, not merely over what remained.
 */
export function lazyGreedy(hooks: GreedyHooks): GreedyResult {
  const heap: HeapEntry[] = [];
  hooks.order.forEach((index, rank) => {
    push(heap, { index, rank, bound: Number.POSITIVE_INFINITY, stamp: -1 });
  });
  const chosen: number[] = [];
  let evaluations = 0;
  let stamp = 0;

  // Each entry can be refreshed at most once per round before it is either
  // picked or deferred, so a round terminates in at most 2n pops. Anything
  // beyond that is a bug in this function, not a property of the data — and on
  // a phone a bug like that is a frozen JS thread, not a stack trace. Fail loud.
  const popBudget = 4 * hooks.order.length + 8;

  while (chosen.length < hooks.budget && heap.length > 0) {
    const deferred: HeapEntry[] = [];
    let picked: HeapEntry | undefined;
    let pops = 0;
    while (heap.length > 0) {
      if ((pops += 1) > popBudget) {
        throw new Error("lazyGreedy failed to converge within one round");
      }
      const top = pop(heap)!;
      if (hooks.blocked(top.index)) {
        deferred.push(top);
        continue;
      }
      if (top.stamp === stamp) {
        picked = top;
        break;
      }
      top.bound = hooks.marginal(top.index);
      top.stamp = stamp;
      evaluations += 1;
      push(heap, top);
    }
    if (!picked) {
      // Everything feasible is exhausted. Relaxing a cap is the only way the
      // album does not come back short, and the caller owns the order.
      for (const entry of deferred) push(heap, entry);
      if (!hooks.relax()) break;
      continue;
    }
    hooks.commit(picked.index, picked.bound);
    chosen.push(picked.index);
    stamp += 1;
    for (const entry of deferred) push(heap, entry);
  }
  return { chosen, evaluations };
}

/**
 * The same walk with every marginal recomputed every round. Only the test uses
 * it: it is the reference `lazyGreedy` must reproduce exactly, and the
 * evaluation count it burns is what makes "lazy" a measurable claim.
 */
export function naiveGreedy(hooks: GreedyHooks): GreedyResult {
  const chosen: number[] = [];
  let evaluations = 0;
  while (chosen.length < hooks.budget) {
    let best: { index: number; rank: number; gain: number } | undefined;
    for (let rank = 0; rank < hooks.order.length; rank += 1) {
      const index = hooks.order[rank];
      if (chosen.includes(index) || hooks.blocked(index)) continue;
      const gain = hooks.marginal(index);
      evaluations += 1;
      if (!best || gain > best.gain) best = { index, rank, gain };
    }
    if (!best) {
      if (!hooks.relax()) break;
      continue;
    }
    hooks.commit(best.index, best.gain);
    chosen.push(best.index);
  }
  return { chosen, evaluations };
}

function higher(left: HeapEntry, right: HeapEntry) {
  if (left.bound !== right.bound) return left.bound > right.bound;
  return left.rank < right.rank;
}

function push(heap: HeapEntry[], entry: HeapEntry) {
  heap.push(entry);
  let child = heap.length - 1;
  while (child > 0) {
    const parent = (child - 1) >> 1;
    if (!higher(heap[child], heap[parent])) break;
    [heap[child], heap[parent]] = [heap[parent], heap[child]];
    child = parent;
  }
}

function pop(heap: HeapEntry[]): HeapEntry | undefined {
  const top = heap[0];
  const last = heap.pop();
  if (heap.length > 0 && last) {
    heap[0] = last;
    let parent = 0;
    for (;;) {
      const left = parent * 2 + 1;
      const right = left + 1;
      let swap = parent;
      if (left < heap.length && higher(heap[left], heap[swap])) swap = left;
      if (right < heap.length && higher(heap[right], heap[swap])) swap = right;
      if (swap === parent) break;
      [heap[parent], heap[swap]] = [heap[swap], heap[parent]];
      parent = swap;
    }
  }
  return top;
}

function quantize(value: number) {
  return Math.round(value * 1_000_000) / 1_000_000;
}
