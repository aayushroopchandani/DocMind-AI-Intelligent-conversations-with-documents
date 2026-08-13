"use client";

import { useEffect, type RefObject } from "react";

/**
 * Publishes the ribbon's natural height as `--dm-ribbon-height` on the host.
 *
 * The collapse animation in `globals.css` transitions the ribbon's height,
 * which needs a real pixel value on both ends — `auto` does not animate, and
 * a guessed `max-height` finishes its travel early and reads as a snap rather
 * than a fold.
 *
 * `scrollHeight` is the right measurement here: the ribbon clips its own
 * overflow, so it reports the content height whether the ribbon is open or
 * already folded shut. The observers watch the ribbon's *children* — watching
 * the ribbon itself would re-measure in response to the very height this hook
 * sets, which is a loop.
 */
export function useRibbonHeight(
  containerRef: RefObject<HTMLDivElement | null>,
): void {
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let resizeObserver: ResizeObserver | null = null;

    const measure = (ribbon: HTMLElement) => {
      const height = ribbon.scrollHeight;
      if (height <= 0) return;
      container.style.setProperty("--dm-ribbon-height", `${height}px`);
      container.dataset.ribbonMeasured = "true";
    };

    const attach = (): boolean => {
      const ribbon = container.querySelector<HTMLElement>(
        ":scope > div > header",
      );
      if (!ribbon) return false;

      measure(ribbon);
      resizeObserver = new ResizeObserver(() => measure(ribbon));
      for (const child of ribbon.children) resizeObserver.observe(child);
      return true;
    };

    // Univer mounts asynchronously, so the ribbon usually is not there yet.
    if (attach()) {
      return () => resizeObserver?.disconnect();
    }

    const mutationObserver = new MutationObserver(() => {
      if (attach()) mutationObserver.disconnect();
    });
    mutationObserver.observe(container, { childList: true, subtree: true });

    return () => {
      mutationObserver.disconnect();
      resizeObserver?.disconnect();
    };
  }, [containerRef]);
}
