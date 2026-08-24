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
    runsSinceLoad: () => runs,
  };
}

async function releaseModel(pending: Promise<unknown>): Promise<void> {
  try {
    const model = (await pending) as { dispose?: () => void } | undefined;
    // Nitro HybridObjects free their native state on JS GC, but a photo batch
    // allocates almost nothing on the JS heap, so GC may never run before the
    // native arena wins. dispose() is the deterministic release.
    model?.dispose?.();
  } catch {
    // A model that failed to load holds nothing to release.
  }
}
