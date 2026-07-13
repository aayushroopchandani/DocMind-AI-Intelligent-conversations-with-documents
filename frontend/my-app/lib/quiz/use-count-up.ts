"use client";

import { useEffect, useRef, useState } from "react";

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Ease-out count-up from 0 to `target`, respecting reduced-motion. */
export function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(() =>
    prefersReducedMotion() ? target : 0,
  );
  const raf = useRef(0);

  useEffect(() => {
    // Reduced-motion users get the final value from the lazy initializer.
    if (prefersReducedMotion()) return;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(eased * target));
      if (t < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, duration]);

  return value;
}
