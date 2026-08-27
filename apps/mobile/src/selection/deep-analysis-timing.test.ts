// Offline accounting harness: no React Native imports and no real timers.
// @ts-expect-error Node's TypeScript runner requires the source extension.
import { DeepAnalysisTimingCollector } from "./deep-analysis-timing.ts";
import type { ModelLoadEvent } from "../ml/model-cache.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`deep-analysis timing self-check failed: ${message}`);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message}: got ${String(actual)}, want ${String(expected)}`);
}

type Scheduled = { at: number; resolve: () => void };

class VirtualClock {
  nowMs = 0;
  private readonly scheduled: Scheduled[] = [];

  readonly now = (): number => this.nowMs;

  delay(durationMs: number): Promise<void> {
    return new Promise((resolve) => {
      this.scheduled.push({ at: this.nowMs + durationMs, resolve });
    });
  }

  async run<T>(promise: Promise<T>): Promise<T> {
    let settled = false;
    void promise.then(
      () => { settled = true; },
      () => { settled = true; },
    );
    while (!settled) {
      // Let dependencies such as quality-after-face schedule their work.
      for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
      if (settled) break;
      this.scheduled.sort((left, right) => left.at - right.at);
      const next = this.scheduled.shift();
      assert(next, "virtual work stalled before the measured promise settled");
      this.nowMs = next.at;
      next.resolve();
    }
    return promise;
  }
}

type FakePhotoDurations = {
  proxy: number;
  perceptual: number;
  face: number;
  quality: number;
  movenet: number;
  movenetInference: number;
  tinyclip: number;
  tinyclipInference: number;
  tinyclipLoad?: ModelLoadEvent;
};

async function runFakePhoto(
  collector: DeepAnalysisTimingCollector,
  clock: VirtualClock,
  durations: FakePhotoDurations,
): Promise<void> {
  await clock.run(
    collector.measureAwaited("proxy-create", () => clock.delay(durations.proxy)),
  );
  await clock.run(
    collector.measureConcurrentWall(async () => {
      const faces = collector.measureAwaited("face-detect", () =>
        clock.delay(durations.face),
      );
      const quality = faces.then(() =>
        collector.measureAwaited("quality-decode", () =>
          clock.delay(durations.quality),
        ),
      );
      return Promise.all([
        collector.measureAwaited("perceptual", () =>
          clock.delay(durations.perceptual),
        ),
        faces,
        quality,
        collector.measureAwaited("movenet", (timing) => {
          timing.recordInference(durations.movenetInference);
          return clock.delay(durations.movenet);
        }),
        collector.measureAwaited("tinyclip", (timing) => {
          timing.recordInference(durations.tinyclipInference);
          if (durations.tinyclipLoad) {
            timing.recordModelLoad(durations.tinyclipLoad);
          }
          return clock.delay(durations.tinyclip);
        }),
      ]);
    }),
  );
}

const clock = new VirtualClock();
const collector = new DeepAnalysisTimingCollector(clock.now);
await runFakePhoto(collector, clock, {
  proxy: 7,
  perceptual: 10,
  face: 20,
  quality: 15,
  movenet: 40,
  movenetInference: 11,
  tinyclip: 60,
  tinyclipInference: 22,
});
await runFakePhoto(collector, clock, {
  proxy: 9,
  perceptual: 30,
  face: 10,
  quality: 12,
  movenet: 50,
  movenetInference: 13,
  tinyclip: 40,
  tinyclipInference: 24,
  tinyclipLoad: {
    sequence: 2,
    kind: "reload",
    elapsedMs: 5,
    succeeded: true,
  },
});

const summaries = collector.summarize();
const summary = (phase: string, measurement = "awaited-steady") => {
  const found = summaries.find(
    (candidate) =>
      candidate.phase === phase && candidate.measurement === measurement,
  );
  assert(found, `${phase}.${measurement} aggregate is missing`);
  return found;
};

const expected = [
  ["proxy-create", 2, 16, 8, 7, 9],
  ["perceptual", 2, 40, 20, 10, 30],
  ["face-detect", 2, 30, 15, 10, 20],
  ["quality-decode", 2, 27, 13.5, 12, 15],
  ["movenet", 2, 90, 45, 40, 50],
] as const;
for (const [phase, count, total, mean, p50, p95] of expected) {
  const aggregate = summary(phase);
  equal(aggregate.count, count, `${phase} count`);
  equal(aggregate.totalMs, total, `${phase} total`);
  equal(aggregate.meanMs, mean, `${phase} mean`);
  equal(aggregate.p50Ms, p50, `${phase} p50`);
  equal(aggregate.p95Ms, p95, `${phase} p95`);
}

const tinyclipSteady = summary("tinyclip");
equal(tinyclipSteady.count, 1, "TinyCLIP reload sample is excluded from steady count");
equal(tinyclipSteady.totalMs, 60, "TinyCLIP steady total remains unpolluted");
equal(tinyclipSteady.reloadCount, 1, "TinyCLIP reload is counted");
const tinyclipReload = summary("tinyclip", "awaited-reload");
equal(tinyclipReload.count, 1, "TinyCLIP reload await has its own count");
equal(tinyclipReload.totalMs, 40, "TinyCLIP reload await has its own latency");
const movenetInference = summary("movenet", "model-inference");
equal(movenetInference.count, 2, "MoveNet inference count");
equal(movenetInference.totalMs, 24, "MoveNet inference excludes outer await time");
equal(movenetInference.p50Ms, 11, "MoveNet inference p50");
equal(movenetInference.p95Ms, 13, "MoveNet inference p95");
const tinyclipInference = summary("tinyclip", "model-inference");
equal(tinyclipInference.count, 2, "TinyCLIP inference count includes post-reload runs");
equal(tinyclipInference.totalMs, 46, "TinyCLIP inference excludes its reload cost");
equal(tinyclipInference.p50Ms, 22, "TinyCLIP inference p50");
equal(tinyclipInference.p95Ms, 24, "TinyCLIP inference p95");

const concurrent = summary("concurrent-model-group", "concurrent-wall");
equal(concurrent.count, 2, "concurrent total count");
equal(concurrent.totalMs, 110, "concurrent total is max/dependency wall time, not a sum");
equal(concurrent.meanMs, 55, "concurrent total mean");
equal(concurrent.p50Ms, 50, "concurrent total p50");
equal(concurrent.p95Ms, 60, "concurrent total p95");

const summedAwaited =
  summary("perceptual").totalMs +
  summary("face-detect").totalMs +
  summary("quality-decode").totalMs +
  summary("movenet").totalMs +
  summary("tinyclip").totalMs +
  tinyclipReload.totalMs;
assert(
  summedAwaited > concurrent.totalMs,
  "overlapping awaited durations must visibly exceed the Promise.all wall total",
);
assert(
  summaries.some((aggregate) => aggregate.totalMs > 0),
  "vacuity guard: known non-zero fake stages cannot aggregate to all zeros",
);

console.log("deep-analysis timing self-check passed");
