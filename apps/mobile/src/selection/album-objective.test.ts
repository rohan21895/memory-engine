/**
 * Objective components and the maximizer, unit-tested (EXPERT-PLAN M6).
 *
 * Two claims carry the whole milestone and both are cheap to fake:
 *
 *   1. "Lazy greedy is exact." A lazy maximizer that quietly returned a
 *      DIFFERENT set from the exhaustive one would still look fine in an album
 *      — you cannot see the photo it should have picked. So it is checked
 *      against `naiveGreedy` on the real fixture corpus, and the check is then
 *      SABOTAGED by handing both an anti-submodular function, where they must
 *      disagree. If they still agreed there, the equality above would be
 *      proving nothing about laziness.
 *   2. "The objective is submodular." Asserted directly: marginal gains never
 *      grow as the set grows, on the fixture similarity matrices rather than on
 *      a toy.
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { COVERAGE_SATURATION, applyPick, emptyState, gainBreakdown, lazyGreedy, naiveGreedy, objectiveValue, validateProblem } from "./album-objective.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { albumFixtures } from "./album-fixtures.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { blendedSimilarity, DEFAULT_ALBUM_OBJECTIVE } from "./album-planner.ts";
import type { SubmodularProblem } from "./album-objective";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`Album objective self-check failed: ${message}`);
}

// --- 1. Validation refuses exactly what breaks the maximizer ----------------

const tiny: SubmodularProblem = {
  quality: [1, 0.5, 0.25],
  similarity: [
    [1, 0.4, 0.1],
    [0.4, 1, 0.2],
    [0.1, 0.2, 1],
  ],
  importance: [1, 1, 1],
  facilityWeight: 2,
  categories: [
    { weight: 1, groupWeight: [1, 1], membership: [[0], [0], [1]] },
  ],
  saturation: COVERAGE_SATURATION,
};
validateProblem(tiny);

function refuses(mutate: (problem: SubmodularProblem) => SubmodularProblem, why: string) {
  let raised = false;
  try {
    validateProblem(mutate(structuredClone(tiny)));
  } catch {
    raised = true;
  }
  assert(raised, why);
}

// A NEGATIVE similarity is the one that silently corrupts results rather than
// crashing: max(0, sim - best) stops being the true facility increment, so a
// stale bound is no longer an upper bound and the lazy skip accepts the wrong
// item. This is the check that replaces "is the DPP kernel PSD?".
refuses((problem) => {
  (problem.similarity[0] as number[])[1] = -0.2;
  return problem;
}, "a negative similarity must be refused");
refuses((problem) => {
  (problem.similarity[1] as number[])[1] = 0.9;
  return problem;
}, "sim(i,i) must be 1 or facility location cannot represent an item by itself");
refuses((problem) => {
  (problem.importance as number[])[0] = -1;
  return problem;
}, "a negative importance must be refused");
refuses((problem) => ({ ...problem, saturation: 0 }), "a zero saturation must be refused");

// --- 2. Monotone and submodular, on the real fixture geometry ---------------

function problemFor(fixture: { candidates: any[] }, size: number): SubmodularProblem {
  const items = fixture.candidates.slice(0, size).map((candidate) => ({
    ...candidate,
    personIds: candidate.personIds ?? [],
  }));
  const groupIndex = new Map<string, number>();
  const membership = items.map((item) =>
    (item.personIds.length > 0 ? item.personIds : [""]).map((personId: string) => {
      if (!groupIndex.has(personId)) groupIndex.set(personId, groupIndex.size);
      return groupIndex.get(personId)!;
    }),
  );
  return {
    quality: items.map((item) => item.quality),
    similarity: items.map((left) =>
      items.map((right) => blendedSimilarity(left, right, DEFAULT_ALBUM_OBJECTIVE)),
    ),
    importance: items.map((item) => 1 + Math.min(item.personIds.length, 3) / 3),
    facilityWeight: DEFAULT_ALBUM_OBJECTIVE.facilityWeight,
    categories: [
      { weight: 1.2, groupWeight: Array(groupIndex.size).fill(1), membership },
    ],
    saturation: COVERAGE_SATURATION,
  };
}

const geometry = problemFor(albumFixtures()[0], 40);
validateProblem(geometry);

const first = geometry.quality.map((_, index) =>
  gainBreakdown(geometry, emptyState(geometry), index).total,
);
const grown = emptyState(geometry);
for (const index of [3, 11, 19, 27]) applyPick(geometry, grown, index);
const later = geometry.quality.map((_, index) => gainBreakdown(geometry, grown, index).total);

const violations = first.filter((value, index) => later[index] > value + 1e-9).length;
console.log(
  `M6 objective shape ${JSON.stringify({
    items: first.length,
    submodularityViolations: violations,
    facilityShare: +(
      gainBreakdown(geometry, emptyState(geometry), 0).facility / first[0]
    ).toFixed(3),
  })}`,
);
assert(violations === 0, `marginal gains must never grow (${violations} did)`);
assert(
  later.every((value) => value >= -1e-9),
  "monotone: no marginal gain may be negative",
);
assert(
  first.some((value, index) => value - later[index] > 0.01),
  "VACUITY: at least one gain must actually SHRINK, or 'never grows' is trivially true",
);

// Adding a near-copy of something already chosen must be worth almost nothing —
// this is the repulsion a DPP would have supplied.
const clone = emptyState(geometry);
applyPick(geometry, clone, 0);
assert(
  gainBreakdown(geometry, clone, 0).facility === 0,
  "an item already represented adds no facility value",
);

// --- 3. Lazy greedy reproduces exhaustive greedy, for fewer evaluations -----

function run(problem: SubmodularProblem, budget: number, walk: typeof lazyGreedy) {
  const state = emptyState(problem);
  const taken = new Set<number>();
  return walk({
    budget,
    order: problem.quality.map((_, index) => index),
    marginal: (index) => gainBreakdown(problem, state, index).total,
    blocked: (index) => taken.has(index),
    commit: (index) => {
      taken.add(index);
      applyPick(problem, state, index);
    },
    relax: () => false,
  });
}

const lazy = run(geometry, 12, lazyGreedy);
const naive = run(geometry, 12, naiveGreedy);
console.log(
  `M6 lazy greedy ${JSON.stringify({
    lazyEvaluations: lazy.evaluations,
    naiveEvaluations: naive.evaluations,
    saved: +(1 - lazy.evaluations / naive.evaluations).toFixed(3),
  })}`,
);
assert(
  JSON.stringify(lazy.chosen) === JSON.stringify(naive.chosen),
  `lazy greedy must reproduce exhaustive greedy exactly (${lazy.chosen} vs ${naive.chosen})`,
);
assert(
  lazy.evaluations < naive.evaluations,
  `lazy greedy must skip work (${lazy.evaluations} vs ${naive.evaluations})`,
);
assert(
  objectiveValue(geometry, lazy.chosen) > objectiveValue(geometry, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]),
  "the chosen set must beat simply taking the first twelve",
);

// SABOTAGE. The equality above is only meaningful if the two walks CAN differ.
// Feed both an ANTI-submodular function — one whose marginal gains grow as the
// set grows — and the stale-bound skip must now be wrong, because a stale bound
// is no longer an upper bound. If they still agreed here, the agreement above
// would be an artifact of the harness, not evidence about laziness.
function antiSubmodular(walk: typeof lazyGreedy) {
  const taken: number[] = [];
  return walk({
    budget: 6,
    order: Array.from({ length: 14 }, (_, index) => index),
    // Grows with |S|, and the growth is largest for items the naive walk would
    // reach last. Deliberately illegal input.
    marginal: (index) => (index % 7) + taken.length * (index / 14),
    blocked: (index) => taken.includes(index),
    commit: (index) => {
      taken.push(index);
    },
    relax: () => false,
  });
}
const sabotagedLazy = antiSubmodular(lazyGreedy);
const sabotagedNaive = antiSubmodular(naiveGreedy);
console.log(
  `M6 sabotage ${JSON.stringify({
    lazy: sabotagedLazy.chosen,
    naive: sabotagedNaive.chosen,
  })}`,
);
assert(
  JSON.stringify(sabotagedLazy.chosen) !== JSON.stringify(sabotagedNaive.chosen),
  "VACUITY: on a non-submodular function the two walks MUST diverge, or the equivalence test is vacuous",
);

// --- 4. The budget is a budget, and relaxation is the only way past it ------

const capped = run(geometry, 1000, lazyGreedy);
assert(
  capped.chosen.length === geometry.quality.length,
  "greedy stops when nothing feasible is left rather than looping",
);

let relaxCalls = 0;
const relaxed = (() => {
  const state = emptyState(geometry);
  const taken = new Set<number>();
  let ceiling = 3;
  return lazyGreedy({
    budget: 8,
    order: geometry.quality.map((_, index) => index),
    marginal: (index) => gainBreakdown(geometry, state, index).total,
    blocked: (index) => taken.has(index) || index >= ceiling,
    commit: (index) => {
      taken.add(index);
      applyPick(geometry, state, index);
    },
    relax: () => {
      relaxCalls += 1;
      if (ceiling >= 8) return false;
      ceiling += 2;
      return true;
    },
  });
})();
assert(
  relaxed.chosen.length === 8 && relaxCalls > 0,
  `a relaxable cap must fill the budget rather than shorten it (${relaxed.chosen.length}, ${relaxCalls} relaxations)`,
);

console.log("album-objective self-check passed");
