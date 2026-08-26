/**
 * The bounded-concurrency loop the album build runs every stage through.
 *
 * It lives in its own module purely so it can be self-checked off-device:
 * `build-album.ts` imports native modules at the top level and cannot be loaded
 * by the Node test runner, and the cancellation contract here is load-bearing
 * (a cancelled build that keeps decoding photos burns the battery and leaves
 * analysis proxies behind in the cache).
 */

export class AlbumBuildCancelledError extends Error {
  constructor() {
    super("Album build was cancelled.");
    this.name = "AlbumBuildCancelledError";
  }
}

export function throwIfCancelled(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw new AlbumBuildCancelledError();
}

export type MapLimitOptions = {
  signal?: AbortSignal;
  /** Called with the running completed count, in completion order. */
  onComplete?: (completed: number) => void;
  /**
   * Stops a worker before it claims another item while the app cannot do
   * foreground work. The album build uses this to pause on background rather
   * than continuing to decode photos while Android is freezing the process.
   */
  waitUntilRunnable?: () => Promise<void>;
  /**
   * Persist resumable state after every N completed items. The callback is
   * awaited by the worker that reaches the boundary before that worker claims
   * more work.
   */
  checkpointEvery?: number;
  onCheckpoint?: (completed: number) => Promise<void>;
  /** Hand the JS thread back to React after every N completed items. */
  yieldEvery?: number;
};

/** A macrotask yield: React can paint progress before selection continues. */
export function yieldToEventLoop(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/**
 * Run `fn` over `items` with at most `limit` in flight, preserving input order
 * in the result.
 *
 * Cancellation contract:
 *  - every worker re-checks the signal before taking an item and after
 *    finishing one, so an abort stops the run after at most one more item per
 *    worker rather than at the end of the batch;
 *  - the run waits for every worker to unwind before it throws, so each
 *    in-flight item has run its own cleanup (`finally` blocks) first;
 *  - exactly one rejection is surfaced and none are left unobserved.
 */
export async function mapLimit<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
  options: MapLimitOptions = {},
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  let completed = 0;
  const checkpointEvery = positiveInterval(options.checkpointEvery);
  const yieldEvery = positiveInterval(options.yieldEvery);
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      throwIfCancelled(options.signal);
      await options.waitUntilRunnable?.();
      throwIfCancelled(options.signal);
      // `waitUntilRunnable` is an async boundary. Other workers may have
      // claimed the tail while this worker waited, so the bounds check belongs
      // after it rather than in the while condition.
      if (cursor >= items.length) return;
      const index = cursor++;
      results[index] = await fn(items[index], index);
      throwIfCancelled(options.signal);
      completed += 1;
      options.onComplete?.(completed);
      if (
        checkpointEvery !== undefined &&
        completed % checkpointEvery === 0
      ) {
        await options.onCheckpoint?.(completed);
      }
      if (yieldEvery !== undefined && completed % yieldEvery === 0) {
        await yieldToEventLoop();
      }
    }
  });
  // allSettled, not all: on cancellation EVERY worker throws, and Promise.all
  // adopts the first rejection while leaving the rest unobserved - which React
  // Native reports as unhandled promise rejections. Waiting for all of them is
  // also what makes the "no half-written state" guarantee true, because each
  // worker's own cleanup has run by the time this returns.
  const settled = await Promise.allSettled(workers);
  const failure = settled.find((result) => result.status === "rejected");
  if (failure?.status === "rejected") throw failure.reason;
  return results;
}

function positiveInterval(value: number | undefined): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 1
    ? Math.floor(value)
    : undefined;
}
