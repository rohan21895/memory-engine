// @ts-expect-error The Expo app deliberately does not ship Node type declarations.
import { readFileSync } from "node:fs";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`progress pacing self-check failed: ${message}`);
}

/**
 * The progress bar must move at roughly the rate the work is finishing.
 *
 * The owner reported "the progress bar correctness". The endpoint was never the
 * problem -- the build emits `done: totalWork` on completion, so it always
 * lands on 100%, and `BuildingScreen` clamps the fraction into [0,1] so it can
 * neither overflow nor go negative. What was wrong is PACING.
 *
 * `ANALYSIS_WORK_UNITS` says how much one deep-analysis photo counts for
 * against one cheap prepass photo. At 20 it understated the deep stage badly.
 * And this got worse the same day: `capEngaged` used to need more than 500
 * picked photos, so `prepassWork` was almost always zero and this weight never
 * mattered. Now the cap engages on nearly every build.
 */

const source = readFileSync(new URL("../build-album.ts", import.meta.url), "utf8");

const units = Number(source.match(/const ANALYSIS_WORK_UNITS = (\d+);/)?.[1]);
assert(Number.isFinite(units) && units > 0, "ANALYSIS_WORK_UNITS must be discoverable");

// The measured build: 08-29 18:35, 645 picked / 64 analysed.
const PICKED = 645;
const ANALYSED = 64;
const TOTAL_MS = 74_885;
const DEEP_MS = 65_710;

/** The build's own arithmetic, mirrored. */
function barShare(picked: number, analysed: number, workUnits: number) {
  const prepassWork = picked;
  const analysisWork = analysed * workUnits;
  const totalWork = prepassWork + analysisWork + 1;
  return { prepassShare: prepassWork / totalWork, totalWork };
}

const measuredPrepassShareOfTime = (TOTAL_MS - DEEP_MS) / TOTAL_MS; // 0.1225
const { prepassShare } = barShare(PICKED, ANALYSED, units);

// Within five points of reality. Tighter than that would be over-fitting one
// build; looser lets the bar surge to a third and then crawl, which is exactly
// what a user calls "wrong".
assert(
  Math.abs(prepassShare - measuredPrepassShareOfTime) < 0.05,
  `the prepass must occupy about ${(measuredPrepassShareOfTime * 100).toFixed(1)}% of the bar, ` +
    `but at ANALYSIS_WORK_UNITS=${units} it occupies ${(prepassShare * 100).toFixed(1)}%`,
);

// VACUITY: the old value must actually fail this, or the assertion proves
// nothing. At 20 the prepass took a third of the bar for an eighth of the wait.
const old = barShare(PICKED, ANALYSED, 20).prepassShare;
assert(
  Math.abs(old - measuredPrepassShareOfTime) >= 0.05,
  `VACUITY: the previous value must be caught by this bar (it gave ${(old * 100).toFixed(1)}%)`,
);

// --- Monotonicity: the bar must never travel backwards. ----------------------
//
// `completedWork = prepassWork + done * ANALYSIS_WORK_UNITS`, and `done` only
// ever rises, so the sequence is monotonic by construction. Pin the shape that
// makes it so, because a future edit that recomputed `prepassWork` mid-build
// would break it invisibly.
assert(
  source.includes("completedWork = prepassWork + done * ANALYSIS_WORK_UNITS"),
  "completed work must be prepass plus a rising multiple, so the bar cannot go backwards",
);

let previous = -1;
for (let done = 0; done <= ANALYSED; done += 1) {
  const completed = PICKED + done * units;
  assert(completed > previous, `progress must rise at done=${done}`);
  previous = completed;
}
const { totalWork } = barShare(PICKED, ANALYSED, units);
assert(
  previous <= totalWork,
  `the deep stage must not overrun the total (${previous} of ${totalWork})`,
);

// --- The endpoint. -----------------------------------------------------------
//
// Not at risk today, and worth keeping that way: the analysed count can come in
// under the budget, so without this final emission the bar would stop short.
assert(
  /emitProgress\(options\.onProgress, \{\s*done: totalWork,\s*total: totalWork,/.test(source),
  "the build must finish by emitting done === total, whatever the analysed count was",
);

// --- The screen must not be able to render a nonsense fraction. --------------

const screen = readFileSync(
  new URL("../ui/screens/BuildingScreen.tsx", import.meta.url),
  "utf8",
);
assert(
  /Math\.max\(0, Math\.min\(1, progress\.done \/ progress\.total\)\)/.test(screen),
  "the rendered fraction must be clamped into [0,1]",
);
assert(
  /progress\.total > 0/.test(screen),
  "...and must not divide by a zero total",
);
assert(
  screen.includes('accessibilityRole="progressbar"') &&
    screen.includes("accessibilityValue={{ min: 0, max: 100, now: percentage }}"),
  "the bar must report its value to a screen reader",
);

console.log("progress pacing self-check passed");
