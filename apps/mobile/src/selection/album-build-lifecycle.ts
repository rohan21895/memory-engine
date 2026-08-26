import { throwIfCancelled } from "./concurrent-map";

export type AlbumBuildLifecycle = {
  waitUntilForeground: () => Promise<void>;
  dispose: () => void;
};

/**
 * Pause an album build while the app is backgrounded.
 *
 * React Native is imported lazily so the scheduling contract remains usable in
 * Node self-checks. The callback is deliberately fire-and-forget: a normal
 * checkpoint is an atomic local write and must not block AppState delivery.
 */
export async function watchAlbumBuildLifecycle(
  signal: AbortSignal | undefined,
  onBackground: () => Promise<void>,
): Promise<AlbumBuildLifecycle> {
  let foreground = true;
  let disposed = false;
  let removeListener: () => void = () => undefined;

  try {
    const { AppState } = await import("react-native");
    foreground = AppState.currentState === "active";
    const subscription = AppState.addEventListener("change", (state) => {
      const wasForeground = foreground;
      foreground = state === "active";
      if (wasForeground && !foreground) void onBackground();
    });
    removeListener = () => subscription.remove();
  } catch {
    // Off-device and unsupported runtimes stay runnable.
    foreground = true;
  }

  return {
    waitUntilForeground: async () => {
      while (!disposed && !foreground) {
        throwIfCancelled(signal);
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      throwIfCancelled(signal);
    },
    dispose: () => {
      disposed = true;
      removeListener();
    },
  };
}
