"use client";

import { useCallback, useSyncExternalStore } from "react";
import { DESKTOP_MEDIA_QUERY } from "@/lib/data-analysis/constants";

/**
 * True when the viewport can host the three-panel desktop layout.
 * Desktop-first: the server snapshot assumes desktop, and the client
 * corrects it before paint via useSyncExternalStore.
 */
export function useIsDesktop(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    const media = window.matchMedia(DESKTOP_MEDIA_QUERY);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(DESKTOP_MEDIA_QUERY).matches,
    () => true,
  );
}
