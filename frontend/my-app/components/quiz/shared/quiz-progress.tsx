import { cn } from "@/lib/utils";

interface QuizProgressProps {
  /** 1-based index of the question currently on screen. */
  current: number;
  total: number;
  /** Optional correct-answer count. Omitted until backend evaluation exists. */
  score?: number;
  /** How many questions have been answered (for the segmented track). */
  answered: number;
}

/**
 * Slim progress header: an animated aurora fill bar, a segmented dot track,
 * and a live score pill. Purely presentational.
 */
export function QuizProgress({
  current,
  total,
  score,
  answered,
}: QuizProgressProps) {
  const pct = Math.round((answered / total) * 100);

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-muted-foreground">
          Question{" "}
          <span className="text-foreground">{current}</span> / {total}
        </span>
        <span className="rounded-full border border-border bg-muted/50 px-2 py-0.5 font-semibold text-muted-foreground">
          {score === undefined ? `${answered} answered` : `${score} correct`}
        </span>
      </div>

      <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-500 ease-out"
          style={{
            width: `${pct}%`,
            background:
              "linear-gradient(90deg, var(--accent-violet), var(--accent-cyan))",
          }}
        />
      </div>

      <div className="flex gap-1">
        {Array.from({ length: total }).map((_, i) => (
          <span
            key={i}
            className={cn(
              "h-1 flex-1 rounded-full transition-colors duration-300",
              i + 1 === current
                ? "bg-[color:var(--accent-cyan)]"
                : i < current
                  ? "bg-foreground/40"
                  : "bg-muted",
            )}
          />
        ))}
      </div>
    </div>
  );
}
