// @ts-expect-error Node's TypeScript runner requires the source extension.
import { holdScanTask, stopScanService } from "../../modules/photeo-scan-service/src/index.ts";

function assert(value: unknown, message: string): void {
  if (!value) throw new Error(`scan-task self-check failed: ${message}`);
}

/**
 * The headless task's whole job is to stay pending: React Native keeps the JS
 * timer loop alive only while one is unresolved, and the backgrounded scan
 * yields through `setTimeout` once per batch. A task that resolves early stalls
 * the scan; one that never resolves holds the CPU after the scan has ended.
 */

/** Resolves to true only if the promise settled, without waiting on a timer. */
async function settled(promise: Promise<void>): Promise<boolean> {
  const pending = Symbol("pending");
  return (await Promise.race([promise, Promise.resolve(pending)])) !== pending;
}

const held = holdScanTask();
assert(!(await settled(held)), "a fresh hold must stay pending while the scan runs");

// A redundant service start must share the live hold. Its own never-resolving
// promise would leave a task nothing could finish, and the service only stops
// itself once every task has -- so the notification would outlive the scan.
const second = holdScanTask();
await stopScanService();
assert(await settled(held), "stopping the scan must let the original task finish");
assert(await settled(second), "and must finish a duplicate hold with it");

// Scans run more than once per app launch, so releasing must not be a one-shot.
const again = holdScanTask();
assert(!(await settled(again)), "a later scan can hold the task again");
await stopScanService();
assert(await settled(again), "and that later hold is releasable too");

console.log("scan-task self-check passed");
