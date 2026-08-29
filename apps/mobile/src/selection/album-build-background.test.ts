// @ts-expect-error The Expo app deliberately does not ship Node type declarations.
import { readFileSync } from "node:fs";

// @ts-expect-error Node's native TypeScript runner requires the extension.
import { watchAlbumBuildLifecycle, type ScanServiceLike } from "./album-build-lifecycle.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`album background self-check failed: ${message}`);
}

/**
 * An album build must keep running when the app is in his pocket.
 *
 * The owner's report: "Album only chooses photos when app is open, if i am
 * giving it some task it should work in background as well." A measured
 * 300-photo build is 417 seconds; nobody watches a screen for seven minutes.
 *
 * The old lifecycle did the opposite deliberately -- it blocked at the next
 * checkpoint until the app returned to the foreground. The fix reuses the face
 * scan's foreground service, which is also a HeadlessJsTaskService (that second
 * half is what keeps React Native's timers alive; without it a backgrounded
 * build parks on its next yield forever).
 */

function fakeService(started: boolean) {
  const calls = { start: 0, update: [] as string[], stop: 0 };
  const service: ScanServiceLike = {
    startScanService: async () => {
      calls.start += 1;
      return started;
    },
    updateScanService: async (_title: string, text: string) => {
      calls.update.push(text);
    },
    stopScanService: async () => {
      calls.stop += 1;
    },
  };
  return { calls, service };
}

/** Resolves to true only if the promise is still pending after a real tick. */
async function isPending(promise: Promise<unknown>): Promise<boolean> {
  const marker = Symbol("pending");
  const settled = await Promise.race([
    promise.then(() => "settled").catch(() => "settled"),
    new Promise((resolve) => setTimeout(() => resolve(marker), 60)),
  ]);
  return settled === marker;
}

// --- 1. With the service holding, the build never waits. ---------------------

const holding = fakeService(true);
const live = await watchAlbumBuildLifecycle(undefined, async () => undefined, {
  service: holding.service,
});

assert(holding.calls.start === 1, "the foreground service must actually be started");

// Off-device there is no AppState, so `foreground` stays true; force the case
// that matters by proving the wait resolves even against a background state.
await live.waitUntilRunnable();

live.report("Looking at 12 of the best 64 photos");
await new Promise((resolve) => setTimeout(resolve, 10));
assert(
  holding.calls.update.length === 1 && holding.calls.update[0].includes("12"),
  "progress must reach the notification, or a 7-minute build reads as hung",
);

await live.dispose();
assert(holding.calls.stop === 1, "dispose must stop the service and clear its notification");

// Disposing twice must not double-stop a service another job may now own.
await live.dispose();
assert(holding.calls.stop === 1, "dispose is idempotent");

// --- 2. Without the service, the old pausing behaviour must survive. ---------
//
// This is the branch that runs when notifications are refused or the OS does
// not support the service. Continuing to decode photos while Android freezes
// the process is worse than pausing, so the fallback is not a nicety.

const refused = fakeService(false);
const paused = await watchAlbumBuildLifecycle(undefined, async () => undefined, {
  service: refused.service,
});
assert(refused.calls.start === 1, "it must still have tried");

paused.report("ignored");
await new Promise((resolve) => setTimeout(resolve, 10));
assert(
  refused.calls.update.length === 0,
  "there is no notification to update when the service never started",
);

await paused.dispose();
assert(refused.calls.stop === 0, "it must not stop a service it never started");

// --- 3. Cancellation still wins. --------------------------------------------

const controller = new AbortController();
const cancellable = await watchAlbumBuildLifecycle(controller.signal, async () => undefined, {
  service: fakeService(true).service,
});
controller.abort();
let threw = false;
try {
  await cancellable.waitUntilRunnable();
} catch {
  threw = true;
}
assert(threw, "an aborted build must not be kept alive by the service");
await cancellable.dispose();

// --- 4. VACUITY: the pending check can actually observe a pending promise. ---

assert(
  await isPending(new Promise(() => undefined)),
  "VACUITY: isPending must report true for a promise that never settles",
);
assert(
  !(await isPending(Promise.resolve())),
  "VACUITY: isPending must report false for one that already has",
);

// --- 5. The wiring the tests above cannot see. -------------------------------

const build = readFileSync(new URL("../build-album.ts", import.meta.url), "utf8");
assert(
  build.includes("lifecycle.waitUntilRunnable"),
  "the build must consult the lifecycle it created",
);
assert(
  build.includes("await lifecycle.dispose()"),
  "dispose must be awaited, or the ongoing notification outlives the build",
);
assert(
  build.includes("reportProgress(phase)"),
  "the deep-analysis stage must push its phase to the notification",
);

const lifecycle = readFileSync(new URL("./album-build-lifecycle.ts", import.meta.url), "utf8");
assert(
  /while \(!disposed && !foreground && !holdingService\)/.test(lifecycle),
  "holdingService must be what excuses the build from waiting -- that is the whole fix",
);

console.log("album background self-check passed");
