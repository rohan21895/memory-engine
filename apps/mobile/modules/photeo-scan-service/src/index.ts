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
};

/** `undefined` means "not looked up yet"; `null` means "looked up, absent". */
let cached: ScanServiceNative | null | undefined;

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

/** Starts the service. False means the scan must stay in the foreground. */
export async function startScanService(
  title: string,
  text: string,
): Promise<boolean> {
  try {
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

export async function stopScanService(): Promise<void> {
  try {
    const native = await nativeModule();
    await native?.stop();
  } catch {
    // Best effort: the service also dies with the process.
  }
}
