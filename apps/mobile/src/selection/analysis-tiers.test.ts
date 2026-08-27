/**
 * The analysis queue, self-checked.
 *
 * The properties EXPERT-PLAN M2 asks for, in the order it asks for them:
 * user-event jobs outrank backfill, ordering is deterministic, process death
 * resumes, cancellation releases leases and keeps committed work.
 *
 * The interesting half is the vacuity: "user beats backfill" is trivially true
 * of any implementation that sorts by priority and stops there, and such an
 * implementation would hand out photographs in Map order once priorities tie —
 * which is every job inside a single album build. Each claim below is paired
 * with the case that would still pass if the rule were only half implemented.
 */

// One line on purpose: `@ts-expect-error` covers only the next line, so above a
// multi-line import it misses and TS5097 lands on the `from` clause instead.
// @ts-expect-error Node's native TypeScript runner requires the source extension.
import { ANALYSIS_PRIORITY, compareAnalysisJobs, createAnalysisQueue, isPermutationOf, restoreInputOrder } from "./analysis-tiers.ts";
import type { AnalysisJob } from "./analysis-tiers";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`analysis-tiers self-check failed: ${message}`);
}

const DAY = 24 * 60 * 60 * 1_000;
const T0 = Date.UTC(2026, 0, 1);

function job(
  photoId: string,
  priority: number,
  capturedAt?: number,
): AnalysisJob {
  return { photoId, priority, capturedAt };
}

// --- 1. Priority ordering --------------------------------------------------

{
  const queue = createAnalysisQueue();
  // Backfill enqueued FIRST and captured EARLIER, so nothing but priority can
  // put the user's photo in front of it.
  queue.enqueue([job("backfill", ANALYSIS_PRIORITY.chargingBackfill, T0)]);
  queue.enqueue([job("wanted", ANALYSIS_PRIORITY.userCandidate, T0 + 5 * DAY)]);
  assert(
    queue.order()[0].photoId === "wanted",
    "a user-requested candidate must outrank a charging backfill already waiting",
  );
}

// VACUITY. Without this, "sort by priority descending" alone passes everything
// above while leaving equal-priority jobs in whatever order they arrived --
// which is EVERY job inside one album build.
{
  // The ids run OPPOSITE to the capture times on purpose. With "a-late" first
  // alphabetically and first in insertion order, capture time is the only rule
  // that can put "z-early" in front -- so a comparator that quietly skipped it
  // and fell through to the id would still be caught here.
  const queue = createAnalysisQueue();
  queue.enqueue([
    job("a-late", ANALYSIS_PRIORITY.userCandidate, T0 + 9 * DAY),
    job("z-early", ANALYSIS_PRIORITY.userCandidate, T0 + 1 * DAY),
  ]);
  assert(
    queue.order()[0].photoId === "z-early",
    "VACUITY: at equal priority the earlier photograph goes first, not the earlier " +
      "caller and not the alphabetically earlier id",
  );
}
{
  const queue = createAnalysisQueue();
  queue.enqueue([
    job("b", ANALYSIS_PRIORITY.userCandidate),
    job("a", ANALYSIS_PRIORITY.userCandidate),
  ]);
  assert(
    queue.order().map(({ photoId }) => photoId).join("") === "ab",
    "VACUITY: with no capture time the stable id decides, so the order is total",
  );
}
assert(
  compareAnalysisJobs(
    job("x", ANALYSIS_PRIORITY.userCandidate, T0 + 9 * DAY),
    job("y", ANALYSIS_PRIORITY.finalistVerification, T0),
  ) > 0,
  "VACUITY: priority still dominates capture time, so the tie-break is only a tie-break",
);

// Determinism: EXPERT-PLAN section 2 requires the same library to produce the
// same album, and a cancelled build leaves behind whatever the queue ran first.
{
  const jobs = Array.from({ length: 40 }, (_, index) =>
    job(`p-${index}`, ANALYSIS_PRIORITY.userCandidate, T0 + (index % 7) * DAY),
  );
  const forwards = createAnalysisQueue();
  forwards.enqueue(jobs);
  const backwards = createAnalysisQueue();
  backwards.enqueue([...jobs].reverse());
  const ids = (queue: ReturnType<typeof createAnalysisQueue>) =>
    queue.order().map(({ photoId }) => photoId).join(",");
  assert(
    ids(forwards) === ids(backwards),
    "the order must not depend on the order the jobs were handed in",
  );
  // VACUITY: a shuffled input producing an identical string would also be true
  // of a queue that ignored its input entirely, so check it kept everything.
  assert(
    forwards.order().length === 40 && ids(forwards).includes("p-39"),
    "VACUITY: ...and every job handed in must still be there",
  );
}

// --- 2. Promotion ----------------------------------------------------------

{
  const queue = createAnalysisQueue();
  queue.enqueue([job("photo", ANALYSIS_PRIORITY.chargingBackfill, T0)]);
  const added = queue.enqueue([job("photo", ANALYSIS_PRIORITY.finalistVerification, T0)]);
  assert(added === 0, "re-asking for the same photograph must not add a second job");
  assert(queue.pending() === 1, "and must not duplicate the work");
  assert(
    queue.order()[0].priority === ANALYSIS_PRIORITY.finalistVerification,
    "the user's request must promote a job that was already sitting in the backfill",
  );
  // VACUITY: promotion must be one-way, or a backfill sweep would demote the
  // photograph the user is waiting for back behind everything else.
  queue.enqueue([job("photo", ANALYSIS_PRIORITY.maintenance, T0)]);
  assert(
    queue.order()[0].priority === ANALYSIS_PRIORITY.finalistVerification,
    "VACUITY: a lower-priority re-enqueue must NOT demote it",
  );
}

// --- 3. Leases, death and cancellation -------------------------------------

{
  const queue = createAnalysisQueue();
  queue.enqueue([
    job("one", ANALYSIS_PRIORITY.userCandidate, T0),
    job("two", ANALYSIS_PRIORITY.userCandidate, T0 + DAY),
    job("three", ANALYSIS_PRIORITY.userCandidate, T0 + 2 * DAY),
  ]);
  const leased = queue.lease(2);
  assert(
    leased.map(({ photoId }) => photoId).join(",") === "one,two",
    "a lease hands out the front of the queue",
  );
  assert(
    queue.pending() === 1 && queue.leased() === 2,
    "leased work is neither pending nor lost",
  );
  assert(
    queue.lease(5).every(({ photoId }) => photoId === "three"),
    "a second lease must not hand out work that is already in flight",
  );

  // The photograph whose record reached disk is finished.
  queue.commit(leased[0]);
  assert(
    queue.pending() === 0 && queue.leased() === 2,
    "a commit removes the job and touches nothing else",
  );
  // The one whose analysis degraded, or whose build was cancelled, is not.
  queue.release(leased[1]);
  assert(
    queue.pending() === 1 && queue.order()[0].photoId === "two",
    "a released lease becomes pending again -- the work was never done",
  );
  // VACUITY: a `release` that re-queued anything, including committed work,
  // would make a build re-analyse photographs it had already stored.
  queue.release(leased[0]);
  assert(
    queue.pending() === 1,
    "VACUITY: releasing a job that was already committed must add nothing back",
  );
  assert(
    queue.enqueue([job("three", ANALYSIS_PRIORITY.userCandidate, T0 + 2 * DAY)]) === 0,
    "a job still in flight must not be re-added by a second scope asking for it",
  );
}

/**
 * Process death.
 *
 * There is no recovery sweep to test, and that is the design: leases live in
 * memory, and the durable truth of "analysed" is a record in the deep-signal
 * store. A killed process re-derives the pending set as `scope minus store`, so
 * this checks the property that makes that safe -- a queue that never saw the
 * commit produces exactly the same order as one that never ran at all.
 */
{
  const scope = [
    job("a", ANALYSIS_PRIORITY.userCandidate, T0),
    job("b", ANALYSIS_PRIORITY.userCandidate, T0 + DAY),
    job("c", ANALYSIS_PRIORITY.userCandidate, T0 + 2 * DAY),
  ];
  const killed = createAnalysisQueue();
  killed.enqueue(scope);
  killed.lease(2); // in flight when the process died; never committed, never released
  const relaunched = createAnalysisQueue();
  relaunched.enqueue(scope); // nothing was stored, so nothing is filtered out
  assert(
    relaunched.order().map(({ photoId }) => photoId).join(",") === "a,b,c",
    "a relaunch re-derives the whole pending set from the scope",
  );
  // VACUITY: and once a record IS durable, that photograph must drop out --
  // otherwise "resume" would mean "start over" forever.
  const resumed = createAnalysisQueue();
  resumed.enqueue(scope.filter(({ photoId }) => photoId !== "a"));
  assert(
    resumed.order().map(({ photoId }) => photoId).join(",") === "b,c",
    "VACUITY: work whose record survived the kill is not re-queued",
  );
}

// --- 4. The priority table itself ------------------------------------------

const classes = Object.values(ANALYSIS_PRIORITY) as number[];
assert(
  new Set(classes).size === classes.length,
  "two priority classes with the same number cannot order against each other",
);
assert(
  ANALYSIS_PRIORITY.finalistVerification > ANALYSIS_PRIORITY.userCandidate &&
    ANALYSIS_PRIORITY.userCandidate > ANALYSIS_PRIORITY.newlyCaptured &&
    ANALYSIS_PRIORITY.newlyCaptured > ANALYSIS_PRIORITY.chargingBackfill &&
    ANALYSIS_PRIORITY.chargingBackfill > ANALYSIS_PRIORITY.maintenance,
  "anything a user is waiting for must outrank anything happening behind them",
);

// --- 5. Processing order, and putting it back ------------------------------
//
// The queue reorders which photograph is decoded first. Everything downstream
// reads an array of analysis results positionally, so if the reorder is not
// undone exactly, each photograph carries a different photograph's embedding,
// pose and faces -- a well-formed album of the wrong pictures, with nothing
// anywhere that would throw.

{
  const queue = createAnalysisQueue();
  const inputs = ["p3", "p1", "p2", "p0"];
  const capturedAt: Record<string, number> = { p0: T0, p1: T0 + DAY, p2: T0 + 2 * DAY, p3: T0 + 3 * DAY };
  queue.enqueue(
    inputs.map((photoId) => job(photoId, ANALYSIS_PRIORITY.userCandidate, capturedAt[photoId])),
  );
  const indexById = new Map(inputs.map((photoId, index) => [photoId, index]));
  const order = queue.lease(inputs.length).map(({ photoId }) => indexById.get(photoId)!);
  assert(
    isPermutationOf(order, inputs.length),
    "the queue over a set of distinct assets must produce a permutation",
  );
  assert(
    JSON.stringify(order) === JSON.stringify([3, 1, 2, 0]),
    `the queue must actually reorder, got ${JSON.stringify(order)}`,
  );
  // Simulate the analysis: each photograph's result is labelled with the
  // photograph it came from, so a mis-scatter is visible rather than silent.
  const results = order.map((inputIndex) => `signals-of-${inputs[inputIndex]}`);
  const restored = restoreInputOrder(order, results);
  assert(
    JSON.stringify(restored) ===
      JSON.stringify(inputs.map((photoId) => `signals-of-${photoId}`)),
    "restoring must put every photograph's signals back on that photograph",
  );
}

// VACUITY for the scatter. `restoreInputOrder` would satisfy the assertion
// above if it were the identity function -- as long as the permutation happened
// to be the identity. It is not, but a future queue change could make it so,
// and then the check would stop testing anything.
assert(
  JSON.stringify(restoreInputOrder([2, 0, 1], ["b", "c", "a"])) ===
    JSON.stringify(["c", "a", "b"]),
  "VACUITY: a non-identity permutation must actually move elements",
);
assert(
  JSON.stringify(restoreInputOrder([2, 0, 1], ["b", "c", "a"])) !==
    JSON.stringify(["b", "c", "a"]),
  "VACUITY: ...and the identity function must FAIL to reproduce it",
);

// The permutation guard. Checked before any decoding, because after it there is
// no fallback -- a duplicate asset id collapses in the queue and the album
// comes back one photograph short with nothing able to say which.
assert(isPermutationOf([0, 1, 2], 3), "the identity is a permutation");
assert(isPermutationOf([2, 0, 1], 3), "so is a rotation");
assert(!isPermutationOf([0, 1], 3), "a short order is not");
assert(!isPermutationOf([0, 1, 1], 3), "a repeated index is not");
assert(!isPermutationOf([0, 1, -1], 3), "a missing lookup (-1) is not");
assert(!isPermutationOf([0, 1, 3], 3), "an out-of-range index is not");
assert(isPermutationOf([], 0), "an empty analysis is vacuously fine");
{
  // The real shape of the failure: two picked photos sharing one asset id.
  const queue = createAnalysisQueue();
  queue.enqueue([
    job("same", ANALYSIS_PRIORITY.userCandidate, T0),
    job("same", ANALYSIS_PRIORITY.userCandidate, T0),
    job("other", ANALYSIS_PRIORITY.userCandidate, T0 + DAY),
  ]);
  const indexById = new Map([["same", 1], ["other", 2]]);
  const order = queue.lease(3).map(({ photoId }) => indexById.get(photoId) ?? -1);
  assert(
    !isPermutationOf(order, 3),
    "a duplicate asset id must be caught by the guard, not by a short album",
  );
}

console.log("analysis-tiers self-check passed");
