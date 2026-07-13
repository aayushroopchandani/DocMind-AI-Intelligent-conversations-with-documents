"use client";

import { useEffect, useRef, useState } from "react";

export interface CountdownState {
  remainingMs: number;
  /** Remaining time as a 0–1 fraction of the total duration. */
  fraction: number;
  expired: boolean;
}

/**
 * A restartable countdown.
 *
 * Keep this inside the small timer component that displays it — the frequent
 * state updates (rAF or interval) then only re-render that component, never the
 * quiz orchestrator, which merely supplies `onExpire`.
 *
 * @param durationMs total time to count down from
 * @param running    whether the clock is ticking
 * @param resetKey   changing this restarts the countdown from `durationMs`
 * @param onExpire   fired once when time reaches zero
 * @param smooth     rAF updates (smooth rings) vs a 250ms interval (digital)
 */
export function useCountdown(
  durationMs: number,
  running: boolean,
  resetKey: string | number,
  onExpire?: () => void,
  smooth = true,
): CountdownState {
  const [remainingMs, setRemainingMs] = useState(durationMs);
  const startRef = useRef(0);
  const firedRef = useRef(false);
  const onExpireRef = useRef(onExpire);

  useEffect(() => {
    onExpireRef.current = onExpire;
  }, [onExpire]);

  useEffect(() => {
    if (!running) return;

    startRef.current = performance.now();
    firedRef.current = false;

    let frame = 0;
    let interval: ReturnType<typeof setInterval> | undefined;

    const update = () => {
      const left = Math.max(
        0,
        durationMs - (performance.now() - startRef.current),
      );
      setRemainingMs(left);
      if (left <= 0 && !firedRef.current) {
        firedRef.current = true;
        onExpireRef.current?.();
      }
    };

    if (smooth) {
      const loop = () => {
        update();
        if (!firedRef.current) frame = requestAnimationFrame(loop);
      };
      frame = requestAnimationFrame(loop);
    } else {
      interval = setInterval(update, 250);
    }

    return () => {
      cancelAnimationFrame(frame);
      if (interval) clearInterval(interval);
    };
  }, [durationMs, running, resetKey, smooth]);

  return {
    remainingMs,
    fraction:
      durationMs > 0 ? Math.min(1, Math.max(0, remainingMs / durationMs)) : 0,
    expired: remainingMs <= 0,
  };
}
