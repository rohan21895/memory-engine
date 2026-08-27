// @ts-expect-error Node's TypeScript runner requires the source extension.
import { formatJsThreadProfile, jsThreadCpu, jsThreadCpuTotalMs, measureSync, recordSyncCpu, resetJsThreadProfile, startEventLoopLagSampler } from "./js-thread-profile.ts";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`js-thread-profile self-check failed: ${message}`);
  }
}

// --- 1. Synchronous CPU accounting ----------------------------------------

resetJsThreadProfile();
assert(jsThreadCpu().length === 0, "a reset profile must hold nothing");
assert(jsThreadCpuTotalMs() === 0, "a reset profile must total zero");

assert(
  measureSync("a", () => 41 + 1) === 42,
  "measureSync must return the block's value, not swallow it",
);

// A throwing block still spent the thread's time. If the `finally` were a
// `try`/return, a decode that dies of OutOfMemoryError — the exact failure this
// pass keeps hitting — would cost real milliseconds and record none of them.
try {
  measureSync("a", () => {
    throw new Error("decode failed");
  });
  assert(false, "measureSync must not swallow the block's error");
} catch (error) {
  assert(
    error instanceof Error && error.message === "decode failed",
    "measureSync must rethrow the original error",
  );
}
assert(
  jsThreadCpu().find((stat) => stat.label === "a")?.count === 2,
  "the throwing block must still have been counted",
);

// The counters are the arithmetic the report rests on, so check the arithmetic
// rather than the clock: `recordSyncCpu` is the same accumulator `measureSync`
// writes through, with the timing supplied instead of measured.
resetJsThreadProfile();
recordSyncCpu("quality.jpeg-decode", 300);
recordSyncCpu("quality.jpeg-decode", 500);
recordSyncCpu("quality.base64", 100);
recordSyncCpu("tinyclip.normalize", -7); // a backwards clock is not negative work
recordSyncCpu("tinyclip.normalize", Number.NaN);

const stats = jsThreadCpu();
assert(stats[0].label === "quality.jpeg-decode", "the costliest block must sort first");
assert(stats[0].totalMs === 800 && stats[0].count === 2, "totals must sum");
assert(stats[0].meanMs === 400, "the mean must be total/count");
assert(stats[0].maxMs === 500, "max must be the largest single block");
assert(
  jsThreadCpu().find((stat) => stat.label === "tinyclip.normalize")?.totalMs === 0,
  "a negative or non-finite duration must clamp to zero, not corrupt the total",
);
assert(jsThreadCpuTotalMs() === 900, "the grand total must be the sum of every label");

// --- 2. Event-loop lag ----------------------------------------------------
//
// The whole conclusion hangs on this number, so it is driven by a fake clock
// and a fake scheduler rather than by a real timer.

type Scheduled = { run: () => void };

function sampler(periodMs: number) {
  let clock = 0;
  let pending: Scheduled | undefined;
  let cancelled = false;
  const stop = startEventLoopLagSampler({
    periodMs,
    now: () => clock,
    schedule: (run) => {
      pending = { run };
      return () => {
        cancelled = true;
        pending = undefined;
      };
    },
  });
  return {
    /** The timer fires after `actualDelayMs` instead of after `periodMs`. */
    fire(actualDelayMs: number): void {
      const job = pending;
      assert(job !== undefined, "the sampler must have scheduled a tick");
      clock += actualDelayMs;
      pending = undefined;
      job.run();
    },
    stop,
    wasCancelled: () => cancelled,
  };
}

// An idle thread: every tick fires exactly on its period.
{
  const idle = sampler(50);
  for (let tick = 0; tick < 10; tick += 1) idle.fire(50);
  const report = idle.stop();
  assert(report.samples === 10, "every tick must produce a sample");
  assert(report.wallMs === 500, "the wall must be the sampler's own window");
  assert(report.blockedMs === 0, "an on-time timer is not lateness");
  assert(
    report.blockedFraction === 0,
    "an idle thread must report a blocked fraction of 0, not of 1",
  );
  assert(report.maxLagMs === 0, "an idle thread has no worst case");
  assert(idle.wasCancelled(), "stopping must cancel the pending tick");
}

// A saturated thread: a 50 ms timer takes 250 ms to fire, so 200 ms of every
// 250 was unavailable. This is the shape a JS-bound deep-analysis pass makes.
{
  const busy = sampler(50);
  for (let tick = 0; tick < 10; tick += 1) busy.fire(250);
  const report = busy.stop();
  assert(report.wallMs === 2500, "the wall must cover every fire");
  assert(report.blockedMs === 2000, "lateness must be measured against the period");
  assert(report.blockedFraction === 0.8, "800 of every 1000 ms were unavailable");
  assert(report.p50LagMs === 200 && report.maxLagMs === 200, "every tick was 200 ms late");
}

// Long blocks among short ones. The median stays low — which is why the median
// alone would have said the thread was fine — while p95 and max find them.
{
  const spiky = sampler(50);
  for (let tick = 0; tick < 18; tick += 1) spiky.fire(55);
  spiky.fire(3050);
  spiky.fire(9050);
  const report = spiky.stop();
  assert(report.p50LagMs === 5, "the typical tick was 5 ms late");
  assert(report.p95LagMs === 3000, "p95 must reach the tail, not sit in the body");
  assert(report.maxLagMs === 9000, "the worst block must survive into the report");
  assert(
    report.meanLagMs > report.p50LagMs * 10,
    "a mean dragged far above the median is itself the signal of a blocking tail",
  );
}

// Stopping twice returns the same report and schedules nothing further.
{
  const once = sampler(50);
  once.fire(50);
  const first = once.stop();
  assert(once.stop() === first, "stop must be idempotent");
}

// --- 3. SABOTAGE. The idle case must be able to fail. ---------------------
//
// This is the mistake that would be worst here, because it fails toward the
// conclusion: measure lateness from when the tick was SCHEDULED rather than
// from when it was DUE, and a perfectly idle thread reports its own period back
// as lag on every tick — `blockedFraction` 1.0, "the JS thread is saturated",
// M3 redirected on an artefact of the instrument.
//
// Replaying the idle trace through that variant must break the assertions above.

function blockedFractionOf(
  lagOf: (firedAt: number, dueAt: number, previousAt: number) => number,
  delays: readonly number[],
  periodMs: number,
): number {
  let clock = 0;
  let previousAt = 0;
  let dueAt = periodMs;
  let blocked = 0;
  for (const delay of delays) {
    clock += delay;
    blocked += Math.max(0, lagOf(clock, dueAt, previousAt));
    previousAt = clock;
    dueAt = clock + periodMs;
  }
  return clock === 0 ? 0 : Math.min(1, blocked / clock);
}

const idleTrace = Array.from({ length: 10 }, () => 50);
const correct = (firedAt: number, dueAt: number): number => firedAt - dueAt;
const saboteur = (firedAt: number, _dueAt: number, previousAt: number): number =>
  firedAt - previousAt;

assert(
  blockedFractionOf(correct, idleTrace, 50) === 0,
  "VACUITY: the shipped lag rule must report 0 on an idle trace",
);
assert(
  blockedFractionOf(saboteur, idleTrace, 50) === 1,
  "SABOTAGE: measuring from the schedule time must report a FALSE 100% on an idle thread",
);
assert(
  blockedFractionOf(saboteur, idleTrace, 50) !== blockedFractionOf(correct, idleTrace, 50),
  "SABOTAGE: the idle assertion must be able to tell the two rules apart",
);

// --- 4. The reported line -------------------------------------------------

resetJsThreadProfile();
recordSyncCpu("quality.jpeg-decode", 90_000);
recordSyncCpu("quality.pixel-maths", 40_000);
const line = formatJsThreadProfile(148_837, {
  wallMs: 148_837,
  periodMs: 50,
  samples: 2_900,
  meanLagMs: 1_200,
  p50LagMs: 900,
  p95LagMs: 2_104,
  maxLagMs: 9_000,
  blockedMs: 138_418,
  blockedFraction: 0.93,
});
assert(
  line.includes("cpu:130000ms/148837ms(87%)"),
  `the line must state CPU against the stage wall, got: ${line}`,
);
assert(line.includes("blocked:93%"), `the line must state the blocked share, got: ${line}`);
assert(
  line.includes("quality.jpeg-decode:90000ms/1"),
  `the line must name the costliest block, got: ${line}`,
);
resetJsThreadProfile();

console.log("js-thread-profile self-check passed (idle=0%, saturated=80%, saboteur rejected)");
