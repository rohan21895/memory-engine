// @ts-expect-error Node's TypeScript runner requires the source extension.
import { cosine, percentile, spearman, cosineAgreement, neighbourRecall, pairSimilaritySpearman, diverseSelection, selectionChange, verificationShift, fidelityReport, FIDELITY_BARS } from "./quant-fidelity.ts";
// A type-only import is erased before the extension can matter, so unlike the
// value import above it needs no suppression.
import type { Embedding } from "./quant-fidelity.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`quant-fidelity self-check failed: ${message}`);
  }
}

/**
 * The harness that gates quantization, gated itself.
 *
 * Every assertion below that says a good candidate PASSES is paired with one
 * that shows a bad candidate FAILS. Without the pairs this file would happily
 * pass while `fidelityReport` returned `passed: true` unconditionally -- which
 * is the exact shape of the failure that would let a broken quantized model
 * into the app with a green check next to it.
 *
 * The last section is the one worth reading: it proves the metrics are not
 * redundant, by building a candidate that each metric individually approves of
 * and another one catches.
 */

// --- A library with structure, so "neighbour" means something. ---------------

function createRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function normalize(values: number[]): number[] {
  const length = Math.hypot(...values);
  return length === 0 ? values : values.map((value) => value / length);
}

const DIMENSIONS = 16;
const random = createRandom(20260827);

// The last axis is held at zero throughout the fixture and used only by 4b,
// which needs a rescaling that is EXACTLY monotone rather than nearly so.
const FREE = DIMENSIONS - 1;
const withReservedAxis = (values: number[]): number[] => normalize([...values, 0]);

/** Six tight groups of six, well apart -- a stand-in for six moments. */
const centres = Array.from({ length: 6 }, () =>
  withReservedAxis(Array.from({ length: FREE }, () => random() * 2 - 1)),
);
const reference: Embedding[] = centres.flatMap((centre) =>
  Array.from({ length: 6 }, () =>
    withReservedAxis(
      centre.slice(0, FREE).map((value) => value + (random() * 2 - 1) * 0.12),
    ),
  ),
);
const groupOf = (index: number): number => Math.floor(index / 6);

const allPairs: Array<readonly [number, number]> = [];
for (let i = 0; i < reference.length; i += 1) {
  for (let j = i + 1; j < reference.length; j += 1) allPairs.push([i, j]);
}

// --- 1. Primitives, each with the failure it must be able to report. --------

assert(Math.abs(cosine([1, 0], [1, 0]) - 1) < 1e-12, "cosine of a vector with itself is 1");
assert(Math.abs(cosine([1, 0], [0, 1])) < 1e-12, "orthogonal vectors score 0");
assert(Math.abs(cosine([1, 0], [-1, 0]) + 1) < 1e-12, "opposite vectors score -1");
assert(Math.abs(cosine([3, 0], [7, 0]) - 1) < 1e-12, "cosine ignores magnitude");

assert(percentile([1, 2, 3, 4, 5], 0.2) === 1, "p20 of five values is the smallest");
assert(percentile([5, 4, 3, 2, 1], 1) === 5, "p100 is the largest, and input order cannot matter");

assert(Math.abs(spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1) < 1e-12, "monotone agreement is +1");
assert(
  Math.abs(spearman([1, 2, 3, 4], [40, 30, 20, 10]) + 1) < 1e-12,
  "VACUITY: reversed order must report -1, not 1 -- a spearman stuck at +1 would approve everything",
);
// Ties are averaged, not resolved by input order. Two similarity values that
// are genuinely equal must not be handed an arbitrary order that then scores as
// agreement or disagreement depending on which array they arrived in.
assert(
  spearman([1, 2, 3, 4], [5, 5, 9, 9]) === spearman([1, 2, 3, 4], [5, 5, 9, 9]),
  "spearman is deterministic",
);
assert(
  Math.abs(spearman([1, 2, 3, 4], [5, 5, 9, 9]) - spearman([2, 1, 3, 4], [5, 5, 9, 9])) < 1e-12,
  "VACUITY: swapping two TIED values must not change the correlation -- ties resolved by position would",
);
assert(
  Math.abs(spearman([1, 2, 3, 4], [5, 5, 9, 9]) - 0.8944271909999159) < 1e-9,
  "...and the tied-rank value is the averaged-rank one, not 1",
);

// --- 2. An identical candidate passes everything. ---------------------------

const identical = fidelityReport(reference, reference, { pairs: allPairs, albumSize: 6 });
assert(identical.passed, "an unchanged model must pass the gate");
assert(Math.abs(identical.agreement.mean - 1) < 1e-9, "unchanged: mean cosine 1");
assert(Math.abs(identical.agreement.p1 - 1) < 1e-9, "unchanged: p1 cosine 1");
assert(identical.recall.mean === 1, "unchanged: recall@10 is exactly 1");
assert(Math.abs(identical.pairSpearman - 1) < 1e-9, "unchanged: Spearman 1");
assert(identical.selection.changed === 0, "unchanged: the album is the same album");
assert(identical.failures.length === 0, "unchanged: nothing to report");

// --- 3. VACUITY. A destroyed candidate must fail, and fail loudly. ----------
//
// Without this block every assertion in section 2 is satisfied by a
// `fidelityReport` that ignores its arguments and returns passed: true.

const scrambled: Embedding[] = reference.map(() =>
  normalize(Array.from({ length: DIMENSIONS }, () => random() * 2 - 1)),
);
const broken = fidelityReport(reference, scrambled, { pairs: allPairs, albumSize: 6 });
assert(!broken.passed, "VACUITY: a candidate of pure noise must FAIL the gate");
assert(
  broken.recall.mean < FIDELITY_BARS.recallAt10,
  "VACUITY: noise must miss the recall@10 bar",
);
assert(
  broken.agreement.mean < FIDELITY_BARS.meanCosine,
  "VACUITY: noise must miss the mean-cosine bar",
);
assert(
  broken.failures.length >= 3,
  "VACUITY: a total failure must name several bars, not report a single generic miss",
);
assert(
  broken.failures.some((line) => line.includes("recall@10")),
  "VACUITY: the failure text must name the bar that failed, so a report is actionable",
);

// --- 4. The metrics are not redundant. --------------------------------------
//
// This is the section that justifies carrying four numbers instead of one.

// 4a. A candidate that reorders neighbours while keeping mean cosine very high.
//     Mean cosine alone would wave this through; recall is what stops it.
const jittered: Embedding[] = reference.map((row) =>
  normalize(row.map((value) => value + (random() * 2 - 1) * 0.09)),
);
const reordered = fidelityReport(reference, jittered, { pairs: allPairs, albumSize: 6 });
assert(
  reordered.agreement.mean > 0.97,
  "the jittered candidate is close to fp32 on average -- that is the trap",
);
assert(
  reordered.recall.mean < 1,
  "...yet it has reordered neighbours, which recall@10 must be able to see",
);
assert(
  reordered.recall.mean < reordered.agreement.mean,
  "VACUITY: recall must be strictly harsher here than mean cosine, or it is decoration",
);

// 4b. A candidate that keeps every ranking EXACTLY but rescales every
//     similarity. Giving every embedding the same component `c` on the unused
//     axis maps each pairwise cosine s to (s + c^2) / (1 + c^2) -- strictly
//     increasing, so the rank metrics are pinned at 1.0 by construction, while
//     every similarity is dragged toward 1 and the fixed bar quietly moves.
//
//     This is not a contrived failure. It is what a quantized model that
//     shrinks the usable output range does, and it is invisible to every
//     rank-based metric in this file.
const PULL = 1.15;
const compressed: Embedding[] = reference.map((row) =>
  normalize([...row.slice(0, FREE), PULL]),
);

const compressedSpearman = pairSimilaritySpearman(reference, compressed, allPairs);
assert(
  Math.abs(compressedSpearman - 1) < 1e-9,
  "an exactly monotone rescaling keeps the pairwise ORDER, so Spearman approves it completely",
);
assert(
  neighbourRecall(reference, compressed, 10).mean === 1,
  "...and recall@10 approves it too, because no neighbour list changed",
);
assert(
  fidelityReport(reference, compressed, { pairs: allPairs, albumSize: 6 }).recall.mean === 1,
  "...so the embedding-side gate has nothing to object to",
);

// Same-group pairs are genuine, different-group pairs are impostors -- the
// synthetic stand-in for "same person" and "two faces in one photo".
const genuine = allPairs.filter(([i, j]) => groupOf(i) === groupOf(j));
const impostor = allPairs.filter(([i, j]) => groupOf(i) !== groupOf(j));
assert(genuine.length > 0 && impostor.length > 0, "the fixture must contain both kinds of pair");

const BAR = 0.6; // DEFAULT_MERGE_THRESHOLD, the bar the product actually uses.
const rescaled = verificationShift(reference, compressed, genuine, impostor, BAR);
assert(
  rescaled.impostorAcceptAfter > rescaled.impostorAcceptBefore,
  "...while it lets in MORE impostors at the fixed bar, which is the harm Spearman cannot see",
);
assert(
  rescaled.impostorAcceptDelta > 0.05,
  "VACUITY: the rescaling must move the impostor rate materially, or 4b proves nothing",
);
assert(
  rescaled.flipRate > 0,
  "a candidate that changes decisions must report a non-zero flip rate",
);

// And the pairing: an unchanged candidate must flip nothing at the same bar.
const unchangedFaces = verificationShift(reference, reference, genuine, impostor, BAR);
assert(
  unchangedFaces.flipRate === 0,
  "VACUITY: an unchanged model must flip NO decisions -- a flip rate that is always positive is noise",
);
assert(
  unchangedFaces.impostorAcceptBefore === unchangedFaces.impostorAcceptAfter,
  "unchanged: the impostor rate is the number it was",
);
assert(
  unchangedFaces.genuineAcceptBefore > unchangedFaces.impostorAcceptBefore,
  "the fixture must be separable at all, or the verification numbers are meaningless",
);

// --- 5. Selection: the album is chosen by spread, and spread can move. ------

const spread = diverseSelection(reference, 6, 0);
assert(spread.length === 6, "the diversity pass returns what it was asked for");
assert(new Set(spread).size === 6, "it must not pick the same photo twice");
assert(
  new Set(spread.map(groupOf)).size === 6,
  "VACUITY: picking 6 from 6 groups must take one from EACH -- a selector returning 0..5 would fail this",
);

const albumMoved = selectionChange(reference, scrambled, 6, 0);
assert(
  albumMoved.rate > 0,
  "VACUITY: a scrambled candidate must change the album, or selectionChange is a constant",
);
assert(
  albumMoved.kept + albumMoved.changed === albumMoved.size,
  "the album accounting must balance",
);

// --- 6. Matched sets are required, because silently zipping mismatched
//        embedding sets would produce a confident and meaningless number. ----

// The message matters as much as the throw. Falling off the end of the shorter
// set raises a TypeError on its own, so asserting merely "it threw" would be
// satisfied by a harness with no check at all -- and would hand whoever ran it
// a stack trace instead of "your two embedding sets are different sizes".
function refusal(run: () => unknown): string {
  try {
    run();
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
  return "";
}

assert(
  refusal(() => cosineAgreement(reference, reference.slice(1))).includes("matched sets"),
  "comparing sets of different lengths must be refused BY NAME, not by falling off the end",
);
assert(
  refusal(() => neighbourRecall(reference, reference.slice(1), 10)).includes("matched sets"),
  "recall must refuse mismatched sets by name too",
);
assert(
  refusal(() => cosineAgreement(reference, reference)) === "",
  "VACUITY: matched sets must NOT be refused -- a function that always threw would pass the two above",
);

console.log("quant-fidelity self-check passed");
