/**
 * The fast bar path chooses the same bar as the slow one.
 *
 * `graphBarsOnly` evaluates the age probe straight off the stored bytes, using
 * `z = intercept + dot(c, v) / |v|` instead of normalising all 512 components
 * first. That is the same arithmetic rearranged, and it saved 5.9 seconds of JS
 * thread per rebuild on the owner's library — but rearranged floating-point
 * arithmetic sums in a different ORDER, so the two can disagree in the last ulp.
 *
 * A disagreement matters only if it flips a face across `BABY_SCORE_CUT`, which
 * is what picks the bar, which is what decides whether that face can be linked
 * to an adult. So this asserts the DECISION matches, not the score — claiming
 * bit-identity would be a claim I cannot honestly make.
 *
 * Deliberately built to be capable of failing: the fixture is checked to contain
 * both children and adults, and the last block sabotages the intercept to prove
 * a real disagreement is actually caught.
 *
 * Run: node --experimental-strip-types src/faces/face-age-bars.test.ts
 */

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { AGE_COEFFICIENTS, AGE_INTERCEPT, BABY_SCORE_CUT, babyScore } from "./face-age-prior.ts";

function assert(condition: boolean, message: string): void {
  if (!condition) {
    console.error(`FAIL: ${message}`);
    process.exit(1);
  }
}

/** The slow path: normalise every component, then score. */
function slowIsChild(raw: number[]): boolean {
  let squared = 0;
  for (const value of raw) squared += value * value;
  const norm = Math.sqrt(squared);
  const unit = norm > Number.EPSILON ? raw.map((value) => value / norm) : raw;
  return babyScore(unit) > BABY_SCORE_CUT;
}

/** The fast path: one pass over the stored bytes, normalisation divided out. */
function fastIsChild(raw: number[], intercept = AGE_INTERCEPT): boolean {
  let dot = 0;
  let squared = 0;
  for (let d = 0; d < raw.length; d += 1) {
    dot += raw[d] * AGE_COEFFICIENTS[d];
    squared += raw[d] * raw[d];
  }
  const norm = Math.sqrt(squared);
  const z = intercept + (norm > Number.EPSILON ? dot / norm : 0);
  return 1 / (1 + Math.exp(-z)) > BABY_SCORE_CUT;
}

// Deterministic pseudo-random int8 vectors, biased along the probe's own
// direction so the fixture straddles the cut instead of landing all one side.
const DIM = AGE_COEFFICIENTS.length;
let state = 12345;
function nextByte(): number {
  state = (state * 1664525 + 1013904223) >>> 0;
  return ((state >>> 16) % 255) - 127;
}
const fixture: number[][] = [];
for (let i = 0; i < 400; i += 1) {
  const lean = (i / 400) * 2 - 1;
  fixture.push(
    Array.from({ length: DIM }, (_, d) => {
      const value = Math.round(nextByte() * 0.35 + lean * AGE_COEFFICIENTS[d] * 90);
      return Math.max(-127, Math.min(127, value));
    }),
  );
}

const slow = fixture.map(slowIsChild);
const children = slow.filter(Boolean).length;

// Vacuity guard. If the fixture is all adults or all children, agreement is
// trivial and this file proves nothing about the boundary that matters.
assert(
  children > 20 && children < fixture.length - 20,
  `fixture is one-sided (${children} children of ${fixture.length}); it does not ` +
    `straddle BABY_SCORE_CUT, so agreement here would be meaningless`,
);

const disagreements = fixture.filter(
  (raw, i) => fastIsChild(raw) !== slow[i],
).length;
assert(
  disagreements === 0,
  `${disagreements} of ${fixture.length} faces get a DIFFERENT bar from the fast ` +
    `path — a child linked on an adult's looser bar is the fusion this split exists to prevent`,
);

// SABOTAGE: shift the intercept enough to move faces across the cut. If this
// still reports zero disagreements, the comparison above is inert and cannot
// have proven anything.
const sabotaged = fixture.filter(
  (raw, i) => fastIsChild(raw, AGE_INTERCEPT + 1.5) !== slow[i],
).length;
assert(
  sabotaged > 0,
  "shifting the intercept by 1.5 changed no decision, so this test cannot detect " +
    "a real disagreement between the two paths",
);

console.log(
  `age bars: fast and slow paths agree on all ${fixture.length} faces ` +
    `(${children} children, ${fixture.length - children} adults); ` +
    `a sabotaged intercept moves ${sabotaged}, so the check is live`,
);
