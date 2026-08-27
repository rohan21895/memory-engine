import type {
  ModelExecutionTimingRecorder,
  ModelLoadEvent,
} from "../ml/model-cache";

export const DEEP_ANALYSIS_PHASES = [
  "proxy-create",
  "perceptual",
  "face-detect",
  "quality-decode",
  "movenet",
  "tinyclip",
] as const;

export type DeepAnalysisPhase = (typeof DEEP_ANALYSIS_PHASES)[number];

export type DeepAnalysisMeasurement =
  | "awaited-steady"
  | "awaited-cold"
  | "awaited-reload"
  | "model-inference"
  | "concurrent-wall";

export type DeepAnalysisAggregate = {
  phase: DeepAnalysisPhase | "concurrent-model-group";
  measurement: DeepAnalysisMeasurement;
  count: number;
  totalMs: number;
  meanMs: number;
  p50Ms: number;
  p95Ms: number;
  reloadCount: number;
};

export type DurationStats = Omit<
  DeepAnalysisAggregate,
  "phase" | "measurement" | "reloadCount"
>;

type AwaitedSample = {
  elapsedMs: number;
  modelLoad?: ModelLoadEvent;
  inferenceMs?: number;
};

/** One phase's degraded photos for a single build. */
export type DeepAnalysisDegradation = {
  phase: DeepAnalysisPhase;
  /** Photos that lost this phase's signal. */
  count: number;
  /**
   * How many of those named an out-of-memory failure.
   *
   * Separated because it is the only degradation whose cause is the BATCH
   * rather than the photo: an OOM says the pass is running too close to the
   * heap ceiling, and the next photo is as likely to lose its signal as this
   * one was. One decode failing on a corrupt JPEG says nothing of the kind.
   */
  outOfMemory: number;
  /** The first failure seen, with local URIs removed. */
  firstMessage: string;
};

/**
 * Java/native memory exhaustion, however the bridge spells it.
 *
 * Android's ART message is `java.lang.OutOfMemoryError: Failed to allocate a
 * <n> byte allocation with <m> free bytes ...`; a native/JS heap failure
 * reaching JS may only carry "out of memory". Matching the allocation phrasing
 * as well means the size is still greppable in the log line even if the class
 * name was stripped somewhere in between.
 */
const OUT_OF_MEMORY = /outofmemory|out of memory|failed to allocate/iu;

/**
 * A failure as it is safe to print.
 *
 * Same treatment `safeFrameError` gives the face scan's one-shot warning: a
 * decode failure quotes the URI it was handed, and that is a `content://` or
 * `file://` path into the owner's library, which must not reach a log this
 * repo asks people to paste into an issue.
 */
export function describeDegradation(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return (
    message.replace(/(?:content|file):\/\/\S+/gu, "<local-uri>").slice(0, 200) ||
    "(no message)"
  );
}

/**
 * Batch-scoped deep-analysis accounting.
 *
 * `measureAwaited` deliberately measures the promise as observed by its
 * caller. Those durations can overlap, and MoveNet/TinyCLIP also include time
 * waiting in their native interpreter queues. They are latency attribution,
 * not independent CPU-cost buckets. `measureConcurrentWall` is the per-photo
 * Promise.all wall clock and is the only total for that concurrent group.
 */
export class DeepAnalysisTimingCollector {
  private readonly awaited = new Map<DeepAnalysisPhase, AwaitedSample[]>();
  private readonly degraded = new Map<DeepAnalysisPhase, DeepAnalysisDegradation>();
  private readonly concurrentWall: number[] = [];
  private readonly now: () => number;

  constructor(now: () => number = Date.now) {
    this.now = now;
  }

  /**
   * One photo lost one phase's signal. Safe to call from inside a catch.
   *
   * Never throws: a build must not fail because its own accounting did.
   */
  recordDegraded(phase: DeepAnalysisPhase, error: unknown): void {
    const message = describeDegradation(error);
    const existing = this.degraded.get(phase);
    if (existing) {
      existing.count += 1;
      if (OUT_OF_MEMORY.test(message)) existing.outOfMemory += 1;
      return;
    }
    this.degraded.set(phase, {
      phase,
      count: 1,
      outOfMemory: OUT_OF_MEMORY.test(message) ? 1 : 0,
      firstMessage: message,
    });
  }

  /**
   * Every phase, in pipeline order, including the ones that lost nothing.
   *
   * A zero is reported rather than omitted on purpose: "quality-decode:0" is
   * evidence the counter ran and found nothing, which an absent field is not.
   */
  degradations(): DeepAnalysisDegradation[] {
    return DEEP_ANALYSIS_PHASES.map(
      (phase) =>
        this.degraded.get(phase) ?? {
          phase,
          count: 0,
          outOfMemory: 0,
          firstMessage: "",
        },
    );
  }

  /**
   * `observer` is told about every degradation this phase reports, in addition
   * to the batch counter. The caller uses it to count PHOTOS rather than
   * signals: photos are analysed concurrently, so nothing outside this
   * per-invocation closure can attribute a degradation to the right one.
   */
  async measureAwaited<T>(
    phase: DeepAnalysisPhase,
    operation: (timing: ModelExecutionTimingRecorder) => Promise<T>,
    observer?: (error: unknown) => void,
  ): Promise<T> {
    const startedAt = this.now();
    let modelLoad: ModelLoadEvent | undefined;
    let inferenceMs: number | undefined;
    const timing: ModelExecutionTimingRecorder = {
      recordModelLoad: (event) => {
        modelLoad = event;
      },
      recordInference: (elapsedMs) => {
        inferenceMs = Math.max(0, elapsedMs);
      },
      recordDegraded: (error) => {
        this.recordDegraded(phase, error);
        try {
          observer?.(error);
        } catch {
          // A caller's counter must not fail the build it is counting.
        }
      },
    };
    try {
      return await operation(timing);
    } finally {
      const samples = this.awaited.get(phase) ?? [];
      samples.push({
        elapsedMs: Math.max(0, this.now() - startedAt),
        modelLoad,
        inferenceMs,
      });
      this.awaited.set(phase, samples);
    }
  }

  async measureConcurrentWall<T>(operation: () => Promise<T>): Promise<T> {
    const startedAt = this.now();
    try {
      return await operation();
    } finally {
      this.concurrentWall.push(Math.max(0, this.now() - startedAt));
    }
  }

  summarize(): DeepAnalysisAggregate[] {
    const aggregates: DeepAnalysisAggregate[] = [];
    for (const phase of DEEP_ANALYSIS_PHASES) {
      const samples = this.awaited.get(phase) ?? [];
      const steady = samples.filter((sample) => sample.modelLoad === undefined);
      aggregates.push(
        aggregate(
          phase,
          "awaited-steady",
          steady.map((sample) => sample.elapsedMs),
          samples.filter((sample) => sample.modelLoad?.kind === "reload").length,
        ),
      );
      for (const kind of ["cold", "reload"] as const) {
        const contaminated = samples.filter(
          (sample) => sample.modelLoad?.kind === kind,
        );
        if (contaminated.length > 0) {
          aggregates.push(
            aggregate(
              phase,
              kind === "cold" ? "awaited-cold" : "awaited-reload",
              contaminated.map((sample) => sample.elapsedMs),
              kind === "reload" ? contaminated.length : 0,
            ),
          );
        }
      }
      if (phase === "movenet" || phase === "tinyclip") {
        aggregates.push(
          aggregate(
            phase,
            "model-inference",
            samples.flatMap((sample) =>
              sample.inferenceMs === undefined ? [] : [sample.inferenceMs],
            ),
            samples.filter((sample) => sample.modelLoad?.kind === "reload").length,
          ),
        );
      }
    }
    aggregates.push(
      aggregate(
        "concurrent-model-group",
        "concurrent-wall",
        this.concurrentWall,
        0,
      ),
    );
    return aggregates;
  }
}

function aggregate(
  phase: DeepAnalysisAggregate["phase"],
  measurement: DeepAnalysisMeasurement,
  durations: readonly number[],
  reloadCount: number,
): DeepAnalysisAggregate {
  return {
    phase,
    measurement,
    ...summarizeDurations(durations),
    reloadCount,
  };
}

export function summarizeDurations(
  durations: readonly number[],
): DurationStats {
  const sorted = durations
    .map((duration) => Math.max(0, duration))
    .sort((left, right) => left - right);
  const totalMs = sorted.reduce((sum, duration) => sum + duration, 0);
  return {
    count: sorted.length,
    totalMs,
    meanMs: sorted.length === 0 ? 0 : totalMs / sorted.length,
    p50Ms: percentile(sorted, 0.5),
    p95Ms: percentile(sorted, 0.95),
  };
}

function percentile(sorted: readonly number[], percentileValue: number): number {
  if (sorted.length === 0) return 0;
  const index = Math.max(
    0,
    Math.min(sorted.length - 1, Math.ceil(percentileValue * sorted.length) - 1),
  );
  return sorted[index];
}
