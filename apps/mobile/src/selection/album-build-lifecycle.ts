// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { throwIfCancelled } from "./concurrent-map.ts";

export type AlbumBuildLifecycle = {
  waitUntilRunnable: () => Promise<void>;
  /** Refreshes the ongoing notification so a long build never looks hung. */
  report: (text: string) => void;
  dispose: () => Promise<void>;
};

/**
 * Keeps an album build running while the app is in the background.
 *
 * This used to do the opposite: it watched AppState and blocked the build at
 * the next checkpoint until the user came back. The owner's report was "Album
 * only chooses photos when app is open, if I am giving it some task it should
 * work in background as well" -- and a measured 300-photo build takes 417
 * seconds, which is far too long to ask someone to stare at a screen.
 *
 * The face scan already solved this exact problem, so this borrows its
 * machinery wholesale rather than inventing a second one. `startScanService`
 * starts a foreground service that is ALSO a HeadlessJsTaskService, and both
 * halves are load-bearing: the foreground service stops Android freezing the
 * process, and the headless task is what keeps React Native's timer loop alive.
 * Without the second half, `setTimeout` stops firing on background and the
 * build parks on its next yield forever -- which was measured on this device as
 * 4 photos in 100 seconds where ~530 were expected.
 *
 * When the service will not start (permission refused, unsupported OS), the old
 * behaviour is exactly right and is what happens: pause, and resume on return.
 */
/**
 * The slice of the scan service this needs.
 *
 * Injectable only so the two behaviours that matter -- "runs on when the
 * service holds" and "still pauses when it does not" -- can be tested off the
 * device. Neither is observable from the source text, and a keep-alive that
 * silently fails to keep anything alive is the exact bug this file exists to
 * fix, so it does not get to be untested.
 */
export type ScanServiceLike = {
  startScanService: (title: string, text: string) => Promise<boolean>;
  updateScanService: (title: string, text: string) => Promise<void>;
  stopScanService: () => Promise<void>;
};

export async function watchAlbumBuildLifecycle(
  signal: AbortSignal | undefined,
  onBackground: () => Promise<void>,
  options: { notificationTitle?: string; service?: ScanServiceLike } = {},
): Promise<AlbumBuildLifecycle> {
  const notificationTitle = options.notificationTitle ?? "Photeo";
  let foreground = true;
  let disposed = false;
  let holdingService = false;
  let removeListener: () => void = () => undefined;

  let startScan: ((title: string, text: string) => Promise<boolean>) | undefined;
  let updateScan: ((title: string, text: string) => Promise<void>) | undefined;
  let stopScan: (() => Promise<void>) | undefined;

  try {
    const service = options.service ?? (await import("../../modules/photeo-scan-service/src"));
    startScan = service.startScanService;
    updateScan = service.updateScanService;
    stopScan = service.stopScanService;
    // The caller stops the library scans before building, so the two never
    // contend for this single service. If that ever stops being true, the
    // loser's dispose() would cancel the winner's notification.
    holdingService = (await startScan("Photeo", "Building your album")) === true;
  } catch {
    // Off-device and unsupported runtimes stay runnable, and simply pause.
  }

  try {
    const { AppState } = await import("react-native");
    foreground = AppState.currentState === "active";
    const subscription = AppState.addEventListener("change", (state) => {
      const wasForeground = foreground;
      foreground = state === "active";
      // Still worth persisting on background even when the build continues:
      // the service keeps the process alive, it does not make it immortal.
      if (wasForeground && !foreground) void onBackground();
    });
    removeListener = () => subscription.remove();
  } catch {
    foreground = true;
  }

  return {
    waitUntilRunnable: async () => {
      // `holdingService` is the whole difference. With the service up this
      // never blocks, and the build runs on regardless of what AppState says.
      while (!disposed && !foreground && !holdingService) {
        throwIfCancelled(signal);
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      throwIfCancelled(signal);
    },
    report: (text: string) => {
      if (!holdingService) return;
      void updateScan?.(notificationTitle, text);
    },
    dispose: async () => {
      disposed = true;
      removeListener();
      if (holdingService) {
        holdingService = false;
        await stopScan?.();
      }
    },
  };
}
