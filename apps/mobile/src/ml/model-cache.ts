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
};

export function createModelCache<T>(
  load: () => Promise<T | undefined>,
  runsPerModel: number = RUNS_PER_MODEL,
): ModelCache<T> {
  let pending: Promise<T | undefined> | undefined;
  let runs = 0;

  return {
    async acquire(): Promise<T | undefined> {
      if (pending && runs >= runsPerModel) {
        // Released before the replacement is requested so the swap never holds
        // two interpreters at once.
        await releaseModel(pending);
        pending = undefined;
      }
      if (!pending) {
        runs = 0;
        // A load that rejects has to degrade exactly like a load that returns
        // undefined; every caller of this cache is guarded, not try/catch-free.
        pending = load().catch(() => undefined);
      }
      runs += 1;
      return pending;
    },
    async retire(): Promise<void> {
      if (!pending) return;
      const retiring = pending;
      pending = undefined;
      runs = 0;
      await releaseModel(retiring);
    },
    runsSinceLoad: () => runs,
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
