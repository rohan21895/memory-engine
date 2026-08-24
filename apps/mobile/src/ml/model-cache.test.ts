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

{
  let loads = 0;
  let disposals = 0;
  const cache = createModelCache(
    async () => {
      loads += 1;
      return { id: loads, dispose: () => { disposals += 1; } };
    },
    3,
  );

  const first = await cache.acquire();
  equal(first?.id, 1, "the first acquire loads the model");
  equal(cache.runsSinceLoad(), 1, "the counter starts at one run");
  equal(await cache.acquire().then((model) => model?.id), 1, "the instance is cached");
  equal(await cache.acquire().then((model) => model?.id), 1, "the third run reuses it");
  equal(cache.runsSinceLoad(), 3, "the counter reaches the retirement threshold");

  const fourth = await cache.acquire();
  equal(loads, 2, "the fourth run reloads the model");
  equal(disposals, 1, "the retired interpreter is disposed");
  equal(fourth?.id, 2, "the caller gets the fresh instance");
  equal(cache.runsSinceLoad(), 1, "the counter resets with the new instance");

  await cache.acquire();
  await cache.acquire();
  await cache.acquire();
  equal(loads, 3, "retirement keeps repeating on the same period");
  equal(disposals, 2, "each retired instance is disposed exactly once");
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
  // A model without dispose() (older builds, or a stub) must not break retirement.
  const cache = createModelCache(async () => ({ id: 1 }), 1);
  await cache.acquire();
  equal(await cache.acquire().then((model) => model?.id), 1, "retirement works without dispose()");
}

console.log("model-cache self-check passed");
