// @ts-expect-error Node requires the extension; Metro resolves it too.
import { createModelCache } from "./model-cache.ts";

// Local assert to match the house test style (the app tsconfig has no
// @types/node, so node:test / node:assert are intentionally not imported).
function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`model-cache self-check failed: ${message}`);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message}: got ${String(actual)}, want ${String(expected)}`);
}

// NOTE ON WHAT THIS FILE DELIBERATELY NO LONGER ASSERTS.
//
// It used to count dispose() calls on a fake model and claim "the retired
// interpreter is disposed". That passed while being false in production. In
// fast-tflite 3.0.1 the model is a nitro HybridObject: dispose() exists on the
// prototype, but nitro's base implementation is `virtual void dispose() {}` and
// fast-tflite does not override it. The arena is freed only by
// ~HybridTfliteModel() once the JS object is unreachable and Hermes collects.
// The old test verified a dispose() the TEST ITSELF supplied, so it proved
// nothing about the app. Retirement's real and only lever is dropping the
// reference, so that is what is asserted here.

{
  let loads = 0;
  const cache = createModelCache(async () => ({ id: ++loads }), 3);

  const first = await cache.acquire();
  equal(first?.id, 1, "the first acquire loads the model");
  equal(cache.runsSinceLoad(), 1, "the counter starts at one run");
  equal(await cache.acquire().then((model) => model?.id), 1, "the instance is cached");
  equal(await cache.acquire().then((model) => model?.id), 1, "the third run reuses it");
  equal(cache.runsSinceLoad(), 3, "the counter reaches the retirement threshold");

  const fourth = await cache.acquire();
  equal(loads, 2, "the fourth run reloads the model");
  equal(fourth?.id, 2, "the caller gets the fresh instance, never the retired one");
  equal(cache.runsSinceLoad(), 1, "the counter resets with the new instance");

  await cache.acquire();
  await cache.acquire();
  await cache.acquire();
  equal(loads, 3, "retirement keeps repeating on the same period");
  equal((await cache.acquire())?.id, 3, "and the newest instance is the one served");
}

{
  // retire() is what makes an idle model collectable. Without it the cache holds
  // the interpreter REACHABLE for the life of the process, and a reachable
  // HybridObject can never be reclaimed, because only the destructor frees.
  let loads = 0;
  const cache = createModelCache(async () => ({ id: ++loads }), 400);

  equal((await cache.acquire())?.id, 1, "a model is loaded");
  equal(cache.runsSinceLoad(), 1, "one run served");

  await cache.retire();
  equal(cache.runsSinceLoad(), 0, "retiring resets the run counter");
  equal(loads, 1, "retiring does not itself load anything");

  equal((await cache.acquire())?.id, 2, "the next acquire loads a fresh instance");
  equal(loads, 2, "the retired instance was genuinely dropped, not reused");
}

{
  // Retiring an idle cache must be a no-op, not a spurious load: this runs at
  // the end of every scan, including scans that embedded nothing.
  let loads = 0;
  const cache = createModelCache(async () => ({ id: ++loads }), 400);
  await cache.retire();
  equal(loads, 0, "retiring a cache that never loaded does nothing");
  await cache.retire();
  equal(loads, 0, "and is idempotent");
}

{
  // A load that failed must still be droppable, without resurfacing the error.
  const cache = createModelCache(async () => { throw new Error("no native module"); }, 400);
  equal(await cache.acquire(), undefined, "a failed load is neutral");
  await cache.retire();
  equal(cache.runsSinceLoad(), 0, "retiring a failed load does not throw");
}

{
  // A rejecting load must degrade to undefined exactly like a failed one, and
  // must not poison the cache: the next period retries.
  let loads = 0;
  const cache = createModelCache(
    async () => {
      loads += 1;
      throw new Error("native module unavailable");
    },
    2,
  );

  equal(await cache.acquire(), undefined, "a rejected load resolves to undefined");
  equal(await cache.acquire(), undefined, "the failure is cached for the period");
  equal(loads, 1, "a failed load is not retried on every photo");
  equal(await cache.acquire(), undefined, "the retry also fails neutral");
  equal(loads, 2, "the load is retried once the period elapses");
}

{
  // The whole point of retirement is that native memory goes DOWN, so the cache
  // must never HOLD two interpreters at once: the outgoing reference has to be
  // released before the replacement load is even requested. A ~200MB TFLite
  // arena requested alongside one not yet freed is the OOM this file exists to
  // prevent. (Whether the freed one is reclaimed promptly is up to Hermes — see
  // releaseModel — but the cache must not be the thing pinning it.)
  const events: string[] = [];
  const cache = createModelCache(
    async () => {
      events.push("load-start");
      // A real loadTensorflowModel is asynchronous; make the window observable.
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      events.push("load-end");
      return { id: events.length };
    },
    2,
  );

  for (let run = 0; run < 6; run += 1) await cache.acquire();
  equal(
    events.join(","),
    "load-start,load-end,load-start,load-end,load-start,load-end",
    "loads never interleave: one instance is fully established before the next begins",
  );
}

console.log("model-cache self-check passed");
