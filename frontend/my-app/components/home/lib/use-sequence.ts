"use client";

import { useEffect, useState } from "react";
import { useInView } from "@/components/home/lib/use-in-view";
import { useReducedMotion } from "@/components/home/lib/use-reduced-motion";

interface UseSequenceOptions {
  /** Hold time in ms for each step, in order. Pass a module-level constant. */
  durations: readonly number[];
  /** Restart from step 0 after the last step. */
  loop?: boolean;
}

/**
 * A timed step machine for scripted demos.
 *
 * One `setTimeout` is alive at a time — never a `requestAnimationFrame` loop —
 * and it only runs while the container is on screen. Under reduced motion the
 * final step is derived during render and the timer never starts, so the demo
 * shows a complete result without animating.
 *
 * `cycle` increments on every wrap; children use it as a `key` to remount
 * per-run animations instead of resetting state from an effect.
 */
export function useSequence<T extends HTMLElement = HTMLDivElement>({
  durations,
  loop = true,
}: UseSequenceOptions) {
  const { ref, inView } = useInView<T>({ threshold: 0.3 });
  const reduced = useReducedMotion();
  const last = durations.length - 1;

  const [{ step, cycle }, setRun] = useState({ step: 0, cycle: 0 });

  useEffect(() => {
    if (reduced || !inView) return;

    const id = window.setTimeout(() => {
      setRun((run) => {
        if (run.step < last) return { ...run, step: run.step + 1 };
        return loop ? { step: 0, cycle: run.cycle + 1 } : run;
      });
    }, durations[step]);

    return () => window.clearTimeout(id);
  }, [inView, reduced, step, last, loop, durations]);

  return { ref, step: reduced ? last : step, cycle, reduced, inView };
}
