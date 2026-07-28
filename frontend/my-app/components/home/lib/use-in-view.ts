"use client";

import { useEffect, useRef, useState } from "react";

interface UseInViewOptions {
  /** Fraction of the element that must be visible to count as in view. */
  threshold?: number;
  /** Latch to `true` on first intersection and stop observing. */
  once?: boolean;
}

/**
 * Reports whether the returned ref is on screen.
 *
 * Animations gate on this so nothing burns CPU while scrolled out of view —
 * the single most effective guard against a landing page that heats up a
 * laptop after a minute of scrolling.
 */
export function useInView<T extends HTMLElement = HTMLDivElement>({
  threshold = 0.25,
  once = false,
}: UseInViewOptions = {}) {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          if (once) observer.disconnect();
        } else if (!once) {
          setInView(false);
        }
      },
      { threshold },
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [threshold, once]);

  return { ref, inView };
}
