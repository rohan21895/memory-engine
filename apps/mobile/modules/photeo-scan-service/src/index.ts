/**
 * Foreground-service control for the face scan.
 *
 * The native module is resolved through a LAZY dynamic import, matching how
 * `face-index.ts` reaches `react-native` and `expo-media-library`. Importing
 * `expo` at module scope executes its async-require setup, which reads
 * `__DEV__` and other React Native globals — fine on a device, fatal in the
 * offline test runner, where it takes down every suite that so much as imports
 * the face index.
 *
 * Every function degrades to "not started" rather than throwing. An Expo Go
 * session, a web build, and any APK built before this module existed all have
 * no native side, and the scan must still run there — just foreground-only.
 */

type ScanServiceNative = {
  start(title: string, text: string): Promise<boolean>;
  update(title: string, text: string): Promise<boolean>;
  stop(): Promise<boolean>;
  isSupported(): boolean;
  exportPrivateFile?(name: string): Promise<string | null>;
};

/** `undefined` means "not looked up yet"; `null` means "looked up, absent". */
let cached: ScanServiceNative | null | undefined;

/** Must match `ScanForegroundService.TASK_KEY`. */
export const SCAN_TASK_KEY = "PhoteoScan";

let holdPromise: Promise<void> | null = null;
let releaseHold: (() => void) | null = null;

/**
 * The body of the headless task, and the reason a backgrounded scan progresses
 * at all.
 *
 * It does no work. Its only job is to stay pending: React Native keeps the JS
 * timer loop alive for exactly as long as a headless task is unresolved
 * (`JavaTimerManager.onHeadlessJsTaskStart` restores the choreographer callback
 * that `onHostPause` tore down). The scan loop yields through `setTimeout` once
 * per batch, so without this it parks at the first batch boundary after the app
 * leaves the screen.
 *
 * Registered from the app entry rather than here, so that this module never
 * imports `react-native` at module scope -- doing so executes React Native's
 * global setup, which is fine on a device and fatal in the offline test runner.
 */
export function holdScanTask(): Promise<void> {
  // A redundant start shares the live hold rather than getting one of its own.
  // Handing back a fresh never-resolving promise would leave a task nothing can
  // finish, and the service only stops itself once EVERY task has -- so the
  // notification would outlive the scan. Sharing means one release ends both.
  if (holdPromise) return holdPromise;
  holdPromise = new Promise<void>((resolve) => {
    releaseHold = resolve;
  });
  return holdPromise;
}

/** Lets the headless task finish, which lets the service stop itself. */
function releaseScanTask(): void {
  const resolve = releaseHold;
  releaseHold = null;
  holdPromise = null;
  resolve?.();
}

async function nativeModule(): Promise<ScanServiceNative | null> {
  if (cached !== undefined) return cached;
  try {
    const { requireOptionalNativeModule } = await import("expo");
    cached =
      requireOptionalNativeModule<ScanServiceNative>("PhoteoScanService") ??
      null;
  } catch {
    cached = null;
  }
  return cached;
}

/**
 * Asks for POST_NOTIFICATIONS, which Android 13+ requires before any
 * notification is visible.
 *
 * The service runs either way -- a denied permission leaves it foregrounded
 * with an invisible notification, which was verified on device. But invisible
 * is the wrong outcome: the notification is the user's only handle on work
 * happening while the app is closed, and it is how they can tell the scan is
 * progressing rather than stuck. So it is requested, and a refusal is accepted
 * silently rather than blocking the scan.
 *
 * Uses React Native's built-in PermissionsAndroid rather than adding
 * expo-notifications, which would pull a whole push-notification stack in to
 * ask one question.
 */
async function ensureNotificationPermission(): Promise<void> {
  try {
    const { PermissionsAndroid, Platform } = await import("react-native");
    if (Platform.OS !== "android" || Number(Platform.Version) < 33) return;
    const permission = "android.permission.POST_NOTIFICATIONS" as Parameters<
      typeof PermissionsAndroid.request
    >[0];
    if (await PermissionsAndroid.check(permission)) return;
    await PermissionsAndroid.request(permission);
  } catch {
    // Asking is best-effort; the scan does not depend on the answer.
  }
}

/** Starts the service. False means the scan must stay in the foreground. */
export async function startScanService(
  title: string,
  text: string,
): Promise<boolean> {
  try {
    await ensureNotificationPermission();
    const native = await nativeModule();
    return (await native?.start(title, text)) === true;
  } catch {
    return false;
  }
}

/** Replaces the notification text. Failure is not worth interrupting a scan. */
export async function updateScanService(
  title: string,
  text: string,
): Promise<void> {
  try {
    const native = await nativeModule();
    await native?.update(title, text);
  } catch {
    // A stale progress line is cosmetic.
  }
}

/**
 * Copies a file from the app's private storage to its external files directory,
 * so `adb pull` can retrieve it from a release build. Returns the path, or null
 * when the copy did not happen.
 *
 * The face index is the only interesting subject: tuning clustering against
 * synthetic embeddings has repeatedly disagreed with what the real library does,
 * and without this every experiment costs a rebuild and a five-minute recluster.
 */
export async function exportPrivateFile(name: string): Promise<string | null> {
  try {
    const native = await nativeModule();
    return (await native?.exportPrivateFile?.(name)) ?? null;
  } catch {
    return null;
  }
}

export async function stopScanService(): Promise<void> {
  // Released first and unconditionally. If the native stop throws, or the
  // native side is absent entirely, an unresolved task would go on holding the
  // timer loop -- and therefore the CPU -- for a scan that has already ended.
  releaseScanTask();
  try {
    const native = await nativeModule();
    await native?.stop();
  } catch {
    // Best effort: the service also dies with the process.
  }
}
