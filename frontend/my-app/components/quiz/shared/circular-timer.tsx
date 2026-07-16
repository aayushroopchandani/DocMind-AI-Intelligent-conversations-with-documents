"use client";

import { useCountdown } from "@/lib/quiz/use-countdown";
import { cn } from "@/lib/utils";

interface CircularTimerProps {
  durationMs: number;
  running: boolean;
  /** Changing this restarts the countdown (pass the current question id). */
  resetKey: string | number;
  onExpire?: () => void;
  size?: number;
}

/**
 * Gamified per-question countdown ring. Smoothly animated (rAF) and colour-shifts
 * green → amber → red as time runs out, pulsing in the final seconds. Owns its own
 * high-frequency state so the quiz never re-renders on every frame.
 */
export function CircularTimer({
  durationMs,
  running,
  resetKey,
  onExpire,
  size = 56,
}: CircularTimerProps) {
  const { remainingMs, fraction } = useCountdown(
    durationMs,
    running,
    resetKey,
    onExpire,
    true,
  );

  const seconds = Math.ceil(remainingMs / 1000);
  const low = remainingMs <= 5000;

  const color =
    fraction > 0.5
      ? "var(--accent-cyan)"
      : fraction > 0.25
        ? "var(--accent-amber)"
        : "var(--quiz-incorrect)";

  const stroke = 5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;

  return (
    <div
      role="timer"
      aria-label={`${seconds} seconds remaining`}
      className={cn("relative shrink-0", low && running && "animate-rf-pulse")}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - fraction)}
        />
      </svg>
      <span
        className="absolute inset-0 flex items-center justify-center text-sm font-bold tabular-nums"
        style={{ color }}
      >
        {seconds}
      </span>
    </div>
  );
}
