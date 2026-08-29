// @ts-expect-error The Expo app deliberately does not ship Node type declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { candidateBudget, shouldCapCandidates } from "./candidate-prepass.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`candidate cap self-check failed: ${message}`);
}

/**
 * Picking more photos must never make an album build faster.
 *
 * It did. The cap engaged only above a fixed 500 picked photos, so on the
 * owner's device a 300-photo pick ran uncapped: all 300 went through the deep
 * stage, and the measured build took 417 s against this module's own stated
 * budget of 148.8 s. A 600-photo pick would have been capped to 64 candidates
 * and finished in roughly a quarter of the time.
 *
 * The rule is now the budget the module already computed. `candidateBudget`
 * calls itself "a PRICE, not a constant"; a fixed threshold beside it was the
 * contradiction.
 */

// --- 1. The absurdity itself. ------------------------------------------------
//
// Deep-stage cost is proportional to how many photos are analysed, so the
// analysed count must never fall as the pick grows.

const albumSize = 24;
const analysed = (pickCount: number) =>
  shouldCapCandidates(pickCount, albumSize)
    ? Math.min(candidateBudget(albumSize), pickCount)
    : pickCount;

let previous = 0;
for (const pick of [10, 50, 64, 65, 100, 300, 499, 500, 501, 1000, 5000]) {
  const cost = analysed(pick);
  assert(
    cost >= previous,
    `analysing ${pick} photos costs ${cost} but ${previous} was the cost of a smaller pick`,
  );
  assert(
    cost <= candidateBudget(albumSize) || cost === pick,
    `a capped pick must not exceed the budget (pick ${pick} analysed ${cost})`,
  );
  previous = cost;
}

// The specific case he hit, and the one that used to be cheaper than it.
assert(analysed(300) === 64, `a 300-photo pick must analyse 64, not ${analysed(300)}`);
assert(
  analysed(300) <= analysed(600),
  "a 300-photo pick must never cost more than a 600-photo one",
);

// --- 2. Small picks are still analysed whole. --------------------------------
//
// The cap exists to bound cost, not to throw away photos from an album that
// already fits. Anything at or under the budget must go through untouched.

assert(!shouldCapCandidates(64, albumSize), "a pick inside the budget must not be capped");
assert(!shouldCapCandidates(12, albumSize), "a small pick must not be capped");
assert(shouldCapCandidates(65, albumSize), "one photo over the budget must engage the cap");

// --- 3. It must survive nonsense rather than return an empty album. ----------

assert(!shouldCapCandidates(Number.NaN, albumSize), "a NaN pick count must not cap");
assert(
  shouldCapCandidates(300, Number.NaN),
  "a NaN album size must still fall back to the floor budget and cap",
);

// --- 4. The budget really does move with the price. --------------------------
//
// VACUITY: if the budget were a constant, every assertion above would hold for
// the wrong reason. Halving the measured per-photo cost must raise it.

const cheap = candidateBudget(albumSize, 200);
assert(
  cheap > candidateBudget(albumSize),
  `VACUITY: a cheaper candidate must buy a larger pool (${cheap} vs ${candidateBudget(albumSize)})`,
);
// ...and a slower device must never quietly ship a thinner album.
assert(
  candidateBudget(albumSize, 99_999) === 64,
  "a slow device must floor at 64, not shrink the pool",
);

// --- 5. The build must actually use this, not the old threshold. -------------

const build = readFileSync(new URL("../build-album.ts", import.meta.url), "utf8");
const capLines = build.match(/const capEngaged = .*/g) ?? [];
assert(capLines.length === 2, `expected both capEngaged sites (found ${capLines.length})`);
assert(
  capLines.every((line: string) => line.includes("shouldCapCandidates(photos.length, count)")),
  `every capEngaged site must use the budget rule (got ${capLines.join(" | ")})`,
);
assert(
  !/capEngaged = photos\.length > CANDIDATE_PREPASS_THRESHOLD/.test(build),
  "the fixed 500-photo threshold must not decide the cap again",
);

console.log("candidate cap self-check passed");
