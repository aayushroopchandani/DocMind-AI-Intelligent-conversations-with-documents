"use client";

import { Clock } from "lucide-react";
import { useCountdown } from "@/lib/quiz/use-countdown";
import { cn } from "@/lib/utils";

interface ExamTimerProps {
  durationMs: number;
  running: boolean;
  onExpire?: () => void;
}

function format(ms: number): string {
  const total = Math.ceil(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/**
 * Compact digital countdown for the whole exam. Deliberately restrained — turns
 * amber under a minute and red-pulses in the final 20 seconds.
 */
export function ExamTimer({ durationMs, running, onExpire }: ExamTimerProps) {
  const { remainingMs } = useCountdown(
    durationMs,
    running,
    "exam",
    onExpire,
    false,
  );

  const warn = remainingMs <= 60_000;
  const danger = remainingMs <= 20_000;

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 font-mono text-sm font-semibold tabular-nums transition-colors",
        danger
          ? "border-[color:var(--quiz-incorrect)]/50 bg-[color:var(--quiz-incorrect)]/10 text-quiz-incorrect animate-rf-pulse"
          : warn
            ? "border-[color:var(--accent-amber)]/50 bg-[color:var(--accent-amber)]/10 text-[color:var(--accent-amber)]"
            : "border-border bg-card text-foreground",
      )}
    >
      <Clock className="size-3.5" />
      {format(remainingMs)}
    </div>
  );
}
