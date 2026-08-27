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
  private readonly concurrentWall: number[] = [];
  private readonly now: () => number;

  constructor(now: () => number = Date.now) {
    this.now = now;
  }

  async measureAwaited<T>(
    phase: DeepAnalysisPhase,
    operation: (timing: ModelExecutionTimingRecorder) => Promise<T>,
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
