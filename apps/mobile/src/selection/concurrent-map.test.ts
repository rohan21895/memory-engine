// @ts-expect-error Node requires the extension; Metro resolves it too.
import { AlbumBuildCancelledError, mapLimit, throwIfCancelled } from "./concurrent-map.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`concurrent-map self-check failed: ${message}`);
}

const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

async function main(): Promise<void> {
  // Results keep input order even though completion order does not.
  const ordered = await mapLimit([5, 1, 3], 3, async (value) => {
    for (let step = 0; step < value; step += 1) await tick();
    return value * 2;
  });
  assert(
    JSON.stringify(ordered) === JSON.stringify([10, 2, 6]),
    "results are written back in input order, not completion order",
  );

  // Concurrency is actually bounded.
  let inFlight = 0;
  let peak = 0;
  await mapLimit(Array.from({ length: 20 }, (_, i) => i), 4, async () => {
    inFlight += 1;
    peak = Math.max(peak, inFlight);
    await tick();
    inFlight -= 1;
  });
  assert(peak <= 4, `at most 4 items run at once (peak was ${peak})`);
  assert(peak > 1, "the limit is a ceiling, not a serialization");

  // onComplete reports a monotonic running count that ends at the total.
  const counts: number[] = [];
  await mapLimit(Array.from({ length: 7 }, (_, i) => i), 3, async () => tick(), {
    onComplete: (done) => counts.push(done),
  });
  assert(counts.length === 7, "every completed item reports progress once");
  assert(
    counts.every((value, index) => value === index + 1),
    `progress counts up 1..n without gaps (got ${JSON.stringify(counts)})`,
  );

  // Cancelling mid-run stops promptly and never starts the tail of the batch.
  const controller = new AbortController();
  let started = 0;
  let settled = 0;
  let cancelled = false;
  try {
    await mapLimit(Array.from({ length: 200 }, (_, i) => i), 4, async () => {
      started += 1;
      if (started === 8) controller.abort();
      await tick();
      // A per-item cleanup, like the analysis-proxy delete the real pipeline
      // runs in its finally block.
      settled += 1;
    }, { signal: controller.signal });
  } catch (error) {
    cancelled = error instanceof AlbumBuildCancelledError;
  }
  assert(cancelled, "an aborted run rejects with AlbumBuildCancelledError");
  assert(
    started < 200,
    `cancellation stops before the batch ends (started ${started} of 200)`,
  );
  assert(
    started <= 8 + 4,
    `each worker stops after at most one more item (started ${started})`,
  );
  assert(
    settled === started,
    `every started item finished its own cleanup before unwinding (${settled} of ${started})`,
  );

  // A signal already aborted before the call does no work at all.
  const preAborted = new AbortController();
  preAborted.abort();
  let ranAfterAbort = false;
  try {
    await mapLimit([1, 2, 3], 2, async () => {
      ranAfterAbort = true;
    }, { signal: preAborted.signal });
    assert(false, "an already-aborted signal must reject");
  } catch (error) {
    assert(error instanceof AlbumBuildCancelledError, "pre-abort rejects with the cancel error");
  }
  assert(!ranAfterAbort, "an already-aborted signal starts no items");

  // A genuine failure surfaces once, and the other workers still unwind.
  let cleanups = 0;
  let message = "";
  try {
    await mapLimit([0, 1, 2, 3, 4, 5], 3, async (value) => {
      try {
        await tick();
        if (value === 1) throw new Error("boom");
      } finally {
        cleanups += 1;
      }
    });
  } catch (error) {
    message = error instanceof Error ? error.message : String(error);
  }
  assert(message === "boom", `the original failure is rethrown (got "${message}")`);
  assert(cleanups >= 3, "sibling workers still ran their cleanup before unwinding");

  // Empty input is a no-op, and throwIfCancelled ignores a missing signal.
  assert(
    JSON.stringify(await mapLimit([], 4, async () => 1)) === "[]",
    "an empty batch resolves to an empty array",
  );
  throwIfCancelled(undefined);

  // eslint-disable-next-line no-console
  console.log("concurrent-map self-check passed");
}

void main();
