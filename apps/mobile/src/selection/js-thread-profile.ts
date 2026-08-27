/**
 * What the single JS thread was actually doing during a build.
 *
 * `DeepAnalysisTimingCollector` measures promises as their CALLER observes
 * them, which is latency attribution: those spans overlap, they include queue
 * wait, and the line it prints says out loud that they must not be summed. That
 * is the right instrument for "how long did this photo wait", and the wrong one
 * for "what was the machine doing", which is the question M3 is stuck on.
 *
 * This module measures the other thing. Two instruments, both about the ONE JS
 * thread every wrapper's preprocessing shares:
 *
 * 1. `measureSync` wraps a block that cannot yield. Because the thread is
 *    single and the block runs to completion, its wall time IS exclusive
 *    JS-thread CPU. These totals DO sum, and they sum against the stage's own
 *    wall clock, so `sum / stageWall` is the fraction of the stage the thread
 *    spent inside JavaScript.
 * 2. `startEventLoopLagSampler` asks a trivial timer to fire on a fixed period
 *    and records how late it actually was. Lateness is time the thread was not
 *    available to anyone — including to the continuation that resolves an
 *    `await model.run(...)`.
 *
 * Why the second one settles an argument the first cannot. `model-inference` is
 * `Date.now()` around `await model.run(...)` (`ml/tinyclip.ts`), and the await
 * resolves on the JS thread. Nitro runs `TfLiteInterpreterInvoke` on a
 * background thread pool (`Promise::async` -> `ThreadPool::shared()`, 3 threads
 * growing to 10), so the span is `native invoke + however long the JS thread
 * took to deliver the resolution`. A device measured 2,280 ms for a graph this
 * Mac runs in 6.03 ms. If `blockedFraction` comes back near 1.0, most of that
 * span is delivery delay and no amount of model or delegate work will move it.
 * If it comes back near 0, the thread was idle and the runtime really is that
 * slow.
 *
 * Neither instrument reads a photo, allocates per pixel, or can throw into the
 * work it measures.
 */

export type SyncCpuStat = {
  label: string;
  count: number;
  totalMs: number;
  meanMs: number;
  maxMs: number;
};

type SyncCpuAccumulator = { count: number; totalMs: number; maxMs: number };

const syncCpu = new Map<string, SyncCpuAccumulator>();

/**
 * Run a SYNCHRONOUS block and charge its wall time to `label`.
 *
 * The block must not await. If it does, the recorded time silently becomes
 * awaited latency — overlapping, unsummable, and indistinguishable from the
 * numbers this module exists to separate from.
 */
export function measureSync<T>(label: string, run: () => T): T {
  const startedAt = Date.now();
  try {
    return run();
  } finally {
    recordSyncCpu(label, Date.now() - startedAt);
  }
}

/** Charge an already-measured synchronous duration to `label`. */
export function recordSyncCpu(label: string, elapsedMs: number): void {
  const duration = Number.isFinite(elapsedMs) ? Math.max(0, elapsedMs) : 0;
  const existing = syncCpu.get(label);
  if (existing) {
    existing.count += 1;
    existing.totalMs += duration;
    if (duration > existing.maxMs) existing.maxMs = duration;
    return;
  }
  syncCpu.set(label, { count: 1, totalMs: duration, maxMs: duration });
}

/** Every measured block, most expensive first. */
export function jsThreadCpu(): SyncCpuStat[] {
  return [...syncCpu.entries()]
    .map(([label, accumulator]) => ({
      label,
      count: accumulator.count,
      totalMs: accumulator.totalMs,
      meanMs: accumulator.count === 0 ? 0 : accumulator.totalMs / accumulator.count,
      maxMs: accumulator.maxMs,
    }))
    .sort((left, right) => right.totalMs - left.totalMs);
}

/** Total JS-thread CPU across every label. Compare this to the stage wall. */
export function jsThreadCpuTotalMs(): number {
  let total = 0;
  for (const accumulator of syncCpu.values()) total += accumulator.totalMs;
  return total;
}

/**
 * Start a fresh window. The counters are module-scoped so a wrapper deep in
 * `src/ml` can charge to them without every caller threading a collector
 * through; a build resets them so one album's numbers are one album's.
 */
export function resetJsThreadProfile(): void {
  syncCpu.clear();
}

export type EventLoopLagReport = {
  /** Wall time the sampler was running. */
  wallMs: number;
  periodMs: number;
  samples: number;
  meanLagMs: number;
  p50LagMs: number;
  p95LagMs: number;
  maxLagMs: number;
  /** Summed lateness: time the thread could not run a trivial task. */
  blockedMs: number;
  /**
   * `blockedMs / wallMs`. Near 1.0 means a promise resolving on a background
   * thread waits roughly this share of the window before JS sees it; near 0
   * means the thread was free and any long span is real work somewhere else.
   */
  blockedFraction: number;
};

export type LagSamplerOptions = {
  periodMs?: number;
  now?: () => number;
  /** Schedules `run` after `delayMs`, returning a cancel function. */
  schedule?: (run: () => void, delayMs: number) => () => void;
};

const DEFAULT_LAG_PERIOD_MS = 50;

/**
 * Sample how late a fixed-period timer actually fires.
 *
 * Lateness is measured against the period rather than against the moment the
 * timer was scheduled, so a sampler that is never blocked reports ~0 rather
 * than reporting its own period back. Returns the stop function; calling it
 * more than once returns the same report and schedules nothing further.
 */
export function startEventLoopLagSampler(
  options: LagSamplerOptions = {},
): () => EventLoopLagReport {
  const periodMs = Math.max(1, options.periodMs ?? DEFAULT_LAG_PERIOD_MS);
  const now = options.now ?? Date.now;
  const schedule =
    options.schedule ??
    ((run: () => void, delayMs: number) => {
      const handle = setTimeout(run, delayMs);
      return () => clearTimeout(handle);
    });

  const startedAt = now();
  const lags: number[] = [];
  let expectedAt = startedAt + periodMs;
  let cancel: (() => void) | undefined;
  let stopped = false;

  const tick = (): void => {
    if (stopped) return;
    const firedAt = now();
    lags.push(Math.max(0, firedAt - expectedAt));
    expectedAt = firedAt + periodMs;
    cancel = schedule(tick, periodMs);
  };
  cancel = schedule(tick, periodMs);

  let report: EventLoopLagReport | undefined;
  return () => {
    if (report) return report;
    stopped = true;
    try {
      cancel?.();
    } catch {
      // A sampler that cannot cancel itself must not fail the build.
    }
    const wallMs = Math.max(0, now() - startedAt);
    const sorted = [...lags].sort((left, right) => left - right);
    const blockedMs = sorted.reduce((sum, lag) => sum + lag, 0);
    report = {
      wallMs,
      periodMs,
      samples: sorted.length,
      meanLagMs: sorted.length === 0 ? 0 : blockedMs / sorted.length,
      p50LagMs: percentile(sorted, 0.5),
      p95LagMs: percentile(sorted, 0.95),
      maxLagMs: sorted.length === 0 ? 0 : sorted[sorted.length - 1],
      blockedMs,
      blockedFraction: wallMs === 0 ? 0 : Math.min(1, blockedMs / wallMs),
    };
    return report;
  };
}

function percentile(sorted: readonly number[], fraction: number): number {
  if (sorted.length === 0) return 0;
  const index = Math.max(
    0,
    Math.min(sorted.length - 1, Math.ceil(fraction * sorted.length) - 1),
  );
  return sorted[index];
}

/**
 * `js-thread={cpu:141203ms/148837ms(95%),blocked:93%,p95lag:2104ms,...}`
 *
 * One line, because the point is a comparison: JS-thread CPU against the wall
 * of the stage that spent it, then the biggest three blocks by name.
 */
export function formatJsThreadProfile(
  stageWallMs: number,
  lag: EventLoopLagReport | undefined,
): string {
  const cpuMs = jsThreadCpuTotalMs();
  const share = stageWallMs > 0 ? Math.round((cpuMs / stageWallMs) * 100) : 0;
  const top = jsThreadCpu()
    .slice(0, 4)
    .map((stat) => `${stat.label}:${Math.round(stat.totalMs)}ms/${stat.count}`)
    .join(",");
  const lagPart = lag
    ? `,blocked:${Math.round(lag.blockedFraction * 100)}%` +
      `,lag-p50:${Math.round(lag.p50LagMs)}ms` +
      `,lag-p95:${Math.round(lag.p95LagMs)}ms` +
      `,lag-max:${Math.round(lag.maxLagMs)}ms`
    : "";
  return `js-thread={cpu:${Math.round(cpuMs)}ms/${Math.round(stageWallMs)}ms(${share}%)${lagPart}${top ? `,${top}` : ""}}`;
}
