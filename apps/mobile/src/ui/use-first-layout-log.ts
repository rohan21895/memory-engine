import { useCallback, useRef } from "react";

/** Release-visible time from screen render start to its first native layout. */
export function useFirstLayoutLog(screen: string): () => void {
  const started = useRef(performance.now());
  const logged = useRef(false);
  return useCallback(() => {
    if (logged.current) return;
    logged.current = true;
    console.log(
      `[PhoteoUI] screen=${screen} first-layout=${(performance.now() - started.current).toFixed(1)}ms`,
    );
  }, [screen]);
}
