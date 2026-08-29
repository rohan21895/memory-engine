/**
 * Keeps one lazily loaded TFLite interpreter per wrapper and retires it every
 * RUNS_PER_MODEL inferences.
 *
 * fast-tflite v3 never gives the interpreter arena back between runs
 * (mrousavy/react-native-fast-tflite#124): native memory climbs from ~200MB to
 * ~1.2GB across a long batch, which OOM-kills the app partway through a
 * 10,000-photo import. The API has no way to reset an interpreter in place, so
 * the only lever is to drop the whole model periodically and let the next
 * inference load a fresh one.
 */

/**
 * Inferences one model instance serves before it is retired. At three models
 * per photo, 400 is roughly one reload per model per 400 photos: the reload
 * (tens of ms, amortised over 400 photos) disappears next to per-photo decode +
 * inference, while the arena never gets long enough to approach the OOM
 * ceiling. Lower it if native memory still creeps on a full-library run.
 */
export const RUNS_PER_MODEL = 400;

export type ModelLoadEvent = {
  sequence: number;
  kind: "cold" | "reload";
  elapsedMs: number;
  succeeded: boolean;
};

export type ModelExecutionTimingRecorder = {
  recordModelLoad(event: ModelLoadEvent): void;
  /** Time spent inside `model.run`, excluding preprocessing, queue wait, and load. */
  recordInference(elapsedMs: number): void;
  /**
   * This photo lost a signal, and why.
   *
   * Every wrapper on the analysis path swallows its own failures and returns
   * `undefined`/`{}`/`[]` so one bad photo cannot fail an album. That is the
   * right behaviour and it stays — but until this existed, the swallow was also
   * the END of the story: a 29MB `OutOfMemoryError` thrown inside a native
   * image-manipulator copy was caught, the album quietly got worse, and the only trace left was
   * a timing sample indistinguishable from a healthy one. `measureAwaited`
   * records in a `finally`, so a rejected phase still produced a duration.
   *
   * Callers must keep catching. They must no longer keep quiet.
   */
  recordDegraded(error: unknown): void;
};

/**
 * Report a swallowed failure without letting the report become the failure.
 *
 * The recorder is optional (an offline caller passes none) and is supplied by
 * the caller, so it is exactly the kind of thing that must not be able to throw
 * out of a catch block and turn one lost signal into a lost album.
 */
export function reportDegraded(
  timing: ModelExecutionTimingRecorder | undefined,
  error: unknown,
): void {
  try {
    timing?.recordDegraded(error);
  } catch {
    // Degradation reporting must never fail the thing it is reporting on.
  }
}

/**
 * One graph's inference cost with NOTHING else on the JS thread.
 *
 * This is the number M3 has been arguing about without. `model-inference` is
 * `Date.now()` around `await model.run(...)`, and the await resolves on the JS
 * thread while five other photos are decoding JPEGs on it — so a 2,280 ms span
 * is `native invoke + delivery delay` and the two cannot be told apart after
 * the fact. Run the same graph when the thread is quiet and the delay term goes
 * to zero, leaving the invoke.
 *
 * On the phone that measured 2,280 ms against this Mac's 6.03 ms, the two
 * outcomes are opposite recommendations:
 *   ~2,000 ms here -> the runtime really is that slow, and only a delegate or a
 *                     thread count can help. Native work is justified.
 *   ~100-300 ms here -> the graph is fine and the span is the JS thread. Native
 *                     work would buy nothing; move the pixel work instead.
 */
export type InferenceBenchmark = {
  runs: number;
  meanMs: number;
  minMs: number;
  maxMs: number;
};

/**
 * Time `runOnce` with the caller's thread otherwise idle.
 *
 * The first run is discarded: it pays for lazy tensor allocation and first
 * touch of the weight arena, which no steady-state photo pays. Bounded by
 * `budgetMs` so a genuinely slow runtime costs a few seconds of a build rather
 * than making the build worse to prove that it was already bad. The budget is
 * checked BETWEEN runs, so the worst case is one warmup plus one measured run:
 * at the device's 2,280 ms that is ~4.6 s per graph, and at a healthy ~120 ms
 * all three runs finish inside half a second. `runs` is reported, so a
 * single-sample result says so rather than passing itself off as a mean of
 * three.
 */
export async function benchmarkInference(
  runOnce: () => Promise<unknown>,
  runs = 3,
  budgetMs = 3000,
  now: () => number = Date.now,
): Promise<InferenceBenchmark | undefined> {
  try {
    const startedAt = now();
    await runOnce();
    const samples: number[] = [];
    for (let index = 0; index < runs; index += 1) {
      if (now() - startedAt > budgetMs) break;
      const runStartedAt = now();
      await runOnce();
      samples.push(Math.max(0, now() - runStartedAt));
    }
    if (samples.length === 0) return undefined;
    const total = samples.reduce((sum, sample) => sum + sample, 0);
    return {
      runs: samples.length,
      meanMs: total / samples.length,
      minMs: Math.min(...samples),
      maxMs: Math.max(...samples),
    };
  } catch {
    // A benchmark must never fail the album it is benchmarking.
    return undefined;
  }
}

export type ModelCacheLoadStats = {
  sequence: number;
  coldLoads: number;
  reloads: number;
  recent: readonly ModelLoadEvent[];
};

export type ModelAcquisition<T> = {
  model: T | undefined;
  /** Present only when this acquire had to load an interpreter. */
  load?: ModelLoadEvent;
};

export type ModelCache<T> = {
  /**
   * The live model, reloading it when the previous one has been retired.
   * Resolves to undefined - never rejects - when loading fails, so callers
   * stay fail-neutral.
   *
   * Call this only from inside the wrapper's serialized inference queue:
   * acquiring can dispose the previous interpreter, which is unsafe while a
   * run is still in flight.
   */
  acquire(): Promise<T | undefined>;
  /** Same acquire, with load attribution for permanent performance timing. */
  acquireWithInfo(): Promise<ModelAcquisition<T>>;
  /**
   * Drops the cached model so its native arena can be collected.
   *
   * Without this the cache holds `pending` for the life of the process, which
   * keeps the interpreter REACHABLE — and since the only thing that frees the
   * arena is the destructor running after GC (see `releaseModel`), a reachable
   * model can never be reclaimed at all. Call it when a batch of work is
   * finished, not between inferences.
   *
   * Safe to call at any time: it must not be called while a run is in flight,
   * for the same reason `acquire()` may only be called from the wrapper's
   * serialized queue.
   */
  retire(): Promise<void>;
  /** Inferences served by the current instance. Exposed for the self-check. */
  runsSinceLoad(): number;
  /** Bounded load history plus lifetime counts; no model or user data. */
  loadStats(): ModelCacheLoadStats;
};

export function createModelCache<T>(
  load: () => Promise<T | undefined>,
  runsPerModel: number = RUNS_PER_MODEL,
  now: () => number = Date.now,
): ModelCache<T> {
  let pending: Promise<T | undefined> | undefined;
  let runs = 0;
  let loadSequence = 0;
  let coldLoads = 0;
  let reloads = 0;
  const recentLoads: ModelLoadEvent[] = [];

  const acquireWithInfo = async (): Promise<ModelAcquisition<T>> => {
    if (pending && runs >= runsPerModel) {
      // Released before the replacement is requested so the swap never holds
      // two interpreters at once.
      await releaseModel(pending);
      pending = undefined;
    }
    let loadEvent: ModelLoadEvent | undefined;
    if (!pending) {
      runs = 0;
      const kind = loadSequence === 0 ? "cold" : "reload";
      const startedAt = now();
      // A load that rejects has to degrade exactly like a load that returns
      // undefined; every caller of this cache is guarded, not try/catch-free.
      pending = load().catch(() => undefined);
      const model = await pending;
      loadSequence += 1;
      if (kind === "cold") coldLoads += 1;
      else reloads += 1;
      loadEvent = {
        sequence: loadSequence,
        kind,
        elapsedMs: Math.max(0, now() - startedAt),
        succeeded: model !== undefined,
      };
      recentLoads.push(loadEvent);
      // This history exists only so one album build can take a before/after
      // snapshot. Keep it bounded for process-long caches.
      if (recentLoads.length > 32) recentLoads.shift();
    }
    runs += 1;
    return { model: await pending, load: loadEvent };
  };

  return {
    async acquire(): Promise<T | undefined> {
      return (await acquireWithInfo()).model;
    },
    acquireWithInfo,
    async retire(): Promise<void> {
      if (!pending) return;
      const retiring = pending;
      pending = undefined;
      runs = 0;
      await releaseModel(retiring);
    },
    runsSinceLoad: () => runs,
    loadStats: () => ({
      sequence: loadSequence,
      coldLoads,
      reloads,
      recent: recentLoads.slice(),
    }),
  };
}

/**
 * Drops the reference to a retired model.
 *
 * There is NO deterministic release available, and it is worth being exact
 * about why, because the obvious call looks like one and is not.
 *
 * `dispose()` IS callable on any nitro HybridObject — the base class registers
 * it on the prototype (nitro-modules 0.37 `HybridObject.cpp`:64). But the base
 * implementation is `virtual void dispose() {}` (`HybridObject.hpp`:108), an
 * empty body, and fast-tflite 3.0.1 does NOT override it. The only code that
 * ever calls `TfLiteInterpreterDelete` is `~HybridTfliteModel()`. So
 * `model.dispose()` runs a real function that does nothing at all, and the
 * arena is reclaimed only when the JS object becomes unreachable AND Hermes
 * collects it.
 *
 * That leaves exactly one lever: stop referencing the model, which is what the
 * caller's `pending = undefined` does. Collection is still at the mercy of a GC
 * that cannot see the native footprint it is holding, so retirement bounds the
 * arena in the long run rather than releasing it on demand.
 */
async function releaseModel(pending: Promise<unknown>): Promise<void> {
  try {
    await pending;
  } catch {
    // A model that failed to load holds nothing to release.
  }
}
