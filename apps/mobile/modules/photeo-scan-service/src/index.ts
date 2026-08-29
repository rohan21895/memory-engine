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
  isBatteryUnrestricted?(): boolean;
  requestBatteryUnrestricted?(): Promise<boolean>;
  openAppSettings?(): Promise<boolean>;
  log?(message: string): boolean;
  thumbnailUri?(assetId: string, size: number): Promise<string | null>;
  albumAnalysisProxy?(
    assetId: string,
    size: number,
  ): Promise<NativeAlbumAnalysisProxy | null>;
  photoFilters?(): string[];
  filteredPhoto?(
    assetId: string,
    filter: string,
    size: number,
  ): Promise<NativeAlbumAnalysisProxy | null>;
  clusterFaces?(
    embeddings: string,
    dim: number,
    assetGroup: number[],
    bars: number[],
    seed: number,
    rounds: number,
  ): Promise<number[] | null>;
};

type NativeAlbumAnalysisProxy = {
  uri?: unknown;
  width?: unknown;
  height?: unknown;
};

export type AlbumAnalysisProxy = {
  uri: string;
  width: number;
  height: number;
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

/**
 * Owners currently holding the foreground service. See `stopScanService`: the
 * service is a singleton and the face scan, the photo index and the album build
 * can each want it, so it is counted rather than toggled.
 */
let serviceHolders = 0;

/** Starts the service. False means the scan must stay in the foreground. */
export async function startScanService(
  title: string,
  text: string,
): Promise<boolean> {
  try {
    await ensureNotificationPermission();
    const native = await nativeModule();
    const started = (await native?.start(title, text)) === true;
    // Counted only on success, so a failed start cannot leave a holder behind
    // that keeps a later stop from ever reaching the native side.
    if (started) serviceHolders += 1;
    return started;
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

/**
 * Whether the OS will let the scan keep working with the screen off.
 *
 * Optimistic when it cannot tell: this only decides whether to ASK, and a
 * prompt the user has already answered is worse than a missed one.
 */
export async function isBatteryUnrestricted(): Promise<boolean> {
  try {
    const native = await nativeModule();
    return native?.isBatteryUnrestricted?.() ?? true;
  } catch {
    return true;
  }
}

/**
 * Shows Android's own "allow background activity?" dialog.
 *
 * Returns whether the dialog was opened, NOT whether it was granted -- the
 * answer arrives later, and the only honest way to learn it is to re-check
 * `isBatteryUnrestricted` after the user comes back.
 */
export async function requestBatteryUnrestricted(): Promise<boolean> {
  try {
    const native = await nativeModule();
    return (await native?.requestBatteryUnrestricted?.()) === true;
  } catch {
    return false;
  }
}

/**
 * Opens this app's OS settings page, where each OEM keeps its own extra
 * background restrictions. ColorOS's "sleep standby optimisation" lives here
 * and has no public intent -- it is deliberately not automatable.
 */
export async function openAppSettings(): Promise<boolean> {
  try {
    const native = await nativeModule();
    return (await native?.openAppSettings?.()) === true;
  } catch {
    return false;
  }
}

/**
 * A small cached thumbnail for one photo, or null to show a placeholder.
 *
 * The grid otherwise paints the full-resolution original into a ~120dp square,
 * so every tile decodes a 12-50 megapixel JPEG. Null is a normal answer, not an
 * error: inaccessible or corrupt media stays a quiet tile. Callers must never
 * substitute the original URI, because that recreates the OOM this API avoids.
 */
export async function thumbnailUri(
  assetId: string,
  size: number,
): Promise<string | null> {
  try {
    const native = await nativeModule();
    return (await native?.thumbnailUri?.(assetId, size)) ?? null;
  } catch {
    return null;
  }
}

/**
 * Returns a 1280px-capable MediaStore proxy whose pixels were bounded before
 * allocation. There is intentionally no original-URI fallback: a missing
 * native module degrades one photo's signals instead of reopening the OOM path.
 */
export async function albumAnalysisProxy(
  assetId: string,
  size: number,
): Promise<AlbumAnalysisProxy | null> {
  try {
    const native = await nativeModule();
    const proxy = await native?.albumAnalysisProxy?.(assetId, size);
    if (
      typeof proxy?.uri !== "string" ||
      typeof proxy.width !== "number" ||
      !Number.isFinite(proxy.width) ||
      proxy.width < 1 ||
      typeof proxy.height !== "number" ||
      !Number.isFinite(proxy.height) ||
      proxy.height < 1
    ) {
      return null;
    }
    return { uri: proxy.uri, width: proxy.width, height: proxy.height };
  } catch {
    return null;
  }
}

/**
 * The filter ids the native side offers, in display order.
 *
 * Asking the module rather than hard-coding a list here keeps one source of
 * truth: a look added to `PhotoFilter` appears in the picker without a matching
 * edit on this side, and one removed cannot leave a swatch that resolves to
 * nothing. The fallback is the identity look, never an empty strip.
 */
export async function photoFilters(): Promise<string[]> {
  try {
    const native = await nativeModule();
    const filters = native?.photoFilters?.();
    if (!Array.isArray(filters) || filters.length === 0) return ["original"];
    return filters.filter((value): value is string => typeof value === "string");
  } catch {
    return ["original"];
  }
}

/**
 * One filtered copy of a photo, bounded exactly like the analysis proxy.
 *
 * `size` is the only difference between a picker swatch and the photo that
 * lands in the album -- both go through here, so what he taps is what he keeps.
 * Null means the look could not be produced and the caller should show the
 * unfiltered photo; it must never fall back to the original URI at full size.
 */
export async function filteredPhoto(
  assetId: string,
  filter: string,
  size: number,
): Promise<AlbumAnalysisProxy | null> {
  try {
    const native = await nativeModule();
    return validProxy(await native?.filteredPhoto?.(assetId, filter, size));
  } catch {
    return null;
  }
}

/** Native returns `null` for missing media, and bad dimensions break layout. */
function validProxy(proxy: NativeAlbumAnalysisProxy | null | undefined): AlbumAnalysisProxy | null {
  if (
    typeof proxy?.uri !== "string" ||
    typeof proxy.width !== "number" ||
    !Number.isFinite(proxy.width) ||
    proxy.width < 1 ||
    typeof proxy.height !== "number" ||
    !Number.isFinite(proxy.height) ||
    proxy.height < 1
  ) {
    return null;
  }
  return { uri: proxy.uri, width: proxy.width, height: proxy.height };
}

/**
 * Resolves the native module ahead of time so a synchronous caller can reach it.
 *
 * Every other function here is async purely because looking the module up needs
 * `await import("expo")` -- importing it at module scope executes React Native's
 * global setup, which is fine on a device and fatal in the offline test runner.
 * Clustering cannot pay that cost: it runs inside `rebuildPeople`, which is
 * synchronous and has six callers. So the lookup is done once, early, and the
 * result cached; `clusterFacesNatively` then needs no await of its own.
 *
 * Safe to call repeatedly -- after the first time it is a resolved promise.
 */
export async function primeNativeModule(): Promise<boolean> {
  await nativeModule();
  return hasNativeClustering();
}

/**
 * Whether native clustering is resolved and callable RIGHT NOW.
 *
 * Callers must be able to ask this before they commit to the graph clusterer,
 * because the fallback is not a slower version of the same thing at library
 * scale -- it is a seventeen-minute frozen app. Measured the hard way: priming
 * used to be fire-and-forget, the first recluster after launch beat the dynamic
 * import, and the TypeScript path took the whole library on one core at 860 MB.
 * Silently degrading was the bug; refusing loudly is the fix.
 */
let mirrored = false;

/**
 * Mirrors this app's own diagnostics into logcat, where a RELEASE build can be
 * measured.
 *
 * React Native bridges `console.log` to logcat in DEVELOPMENT ONLY. Every timing
 * this app already prints -- `rebuildPeople 27850ms`, the scan batches, the
 * consolidation spike -- is therefore invisible on the build the owner actually
 * runs, which is how "the app is slow" kept being answered with guesses instead
 * of numbers. Wrapping the console rather than editing dozens of call sites
 * makes every diagnostic that already exists visible at once, and every future
 * one free.
 *
 * Call after `primeNativeModule`; before that there is nothing to write to.
 */
export function mirrorConsoleToLogcat(): void {
  if (mirrored) return;
  mirrored = true;
  for (const level of ["log", "info", "warn", "error"] as const) {
    const original = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      original(...args);
      try {
        // Only this app's own tagged diagnostics. Mirroring every library's
        // chatter would bury the timeline this exists to expose.
        const first = args[0];
        if (typeof first !== "string" || !first.startsWith("[Photeo")) return;
        cached?.log?.(
          args.map((value) => (typeof value === "string" ? value : String(value))).join(" "),
        );
      } catch {
        // Diagnostics must never be able to break the thing they measure.
      }
    };
  }
}

export function hasNativeClustering(): boolean {
  return typeof cached?.clusterFaces === "function";
}

/**
 * Groups every face in the library natively, returning one label per face.
 *
 * `null` means the native side could not do it -- an older APK, a web build, a
 * lookup that has not been primed yet, or a request it rejected -- and the
 * caller must fall back to the TypeScript clusterer. That fallback is the same
 * algorithm and gives the same answer; it is just far too slow to be the only
 * path, which is the entire reason this exists.
 */
export async function clusterFacesNatively(
  embeddings: string,
  dim: number,
  assetGroup: number[],
  bars: number[],
  seed: number,
  rounds: number,
): Promise<number[] | null> {
  try {
    // ASYNC on purpose. Expo runs a synchronous `Function` on the JS thread, so
    // declared that way this froze the app for the entire six minutes of a
    // whole-library regroup -- faster than the seventeen minutes it replaced,
    // and still a freeze, which is the part the user actually experiences.
    // `AsyncFunction` hands it to a background dispatcher instead.
    const native = await nativeModule();
    const labels = await native?.clusterFaces?.(
      embeddings,
      dim,
      assetGroup,
      bars,
      seed,
      rounds,
    );
    // A short answer is a broken answer, and silently clustering a prefix of the
    // library would scatter people rather than fail.
    return Array.isArray(labels) && labels.length === assetGroup.length
      ? labels
      : null;
  } catch {
    return null;
  }
}

export async function stopScanService(): Promise<void> {
  // One service, several owners: the face scan, the photo index build and now
  // the album build all keep it alive. Without a count, whichever finished
  // first would tear the service out from under the others -- and the loser
  // would not fail loudly, it would quietly stop making progress the moment the
  // screen went off, which is the exact bug this service exists to prevent.
  if (serviceHolders > 0) serviceHolders -= 1;
  if (serviceHolders > 0) return;

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

/** Owners currently keeping the foreground service alive. */
export function scanServiceHolderCount(): number {
  return serviceHolders;
}
