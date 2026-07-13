"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, RotateCcw, Trophy, XCircle } from "lucide-react";
import type { GradedResult, Quiz } from "@/lib/quiz/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface QuizResultProps {
  quiz: Quiz;
  results: GradedResult[];
  onRestart: () => void;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Ease-out count-up for the score, respecting reduced-motion. */
function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(() =>
    prefersReducedMotion() ? target : 0,
  );
  const raf = useRef<number>(0);

  useEffect(() => {
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

export function QuizResult({ quiz, results, onRestart }: QuizResultProps) {
  const total = quiz.questions.length;
  const score = results.filter((r) => r.correct).length;
  const pct = Math.round((score / total) * 100);
  const animatedPct = useCountUp(pct);

  const headline =
    pct >= 80 ? "Excellent work!" : pct >= 50 ? "Nice effort!" : "Keep practicing!";

  // SVG ring geometry.
  const R = 52;
  const C = 2 * Math.PI * R;

  return (
    <div className="animate-quiz-in rounded-2xl border border-border bg-card p-8 sm:p-10">
      <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center sm:gap-8">
        <div className="relative size-32 shrink-0">
          <svg viewBox="0 0 120 120" className="size-full -rotate-90">
            <circle
              cx="60"
              cy="60"
              r={R}
              fill="none"
              stroke="var(--muted)"
              strokeWidth="8"
            />
            <circle
              cx="60"
              cy="60"
              r={R}
              fill="none"
              stroke="url(#quiz-ring)"
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={C}
              strokeDashoffset={C - (animatedPct / 100) * C}
              style={{ transition: "stroke-dashoffset 120ms linear" }}
            />
            <defs>
              <linearGradient id="quiz-ring" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="var(--accent-violet)" />
                <stop offset="100%" stopColor="var(--accent-cyan)" />
              </linearGradient>
            </defs>
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-3xl font-bold text-foreground">
              {animatedPct}%
            </span>
            <span className="text-xs text-muted-foreground">
              {score}/{total}
            </span>
          </div>
        </div>

        <div className="text-center sm:text-left">
          <div className="flex items-center justify-center gap-2 text-[color:var(--accent-amber)] sm:justify-start">
            <Trophy className="size-4" />
            <span className="text-xs font-medium uppercase tracking-wide">
              Practice complete
            </span>
          </div>
          <h2 className="mt-2 text-2xl font-semibold text-foreground">
            {headline}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            You answered {score} of {total} questions correctly.
          </p>
          <Button onClick={onRestart} variant="outline" className="mt-4 h-9">
            <RotateCcw className="size-4" data-icon="inline-start" />
            Retry quiz
          </Button>
        </div>
      </div>

      <div className="mt-8 border-t border-border pt-6">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Question review
        </p>
        <ul className="space-y-1.5">
          {quiz.questions.map((question, i) => {
            const correct = results[i]?.correct;
            const prompt =
              question.type === "true_false"
                ? question.statement
                : question.question;
            return (
              <li
                key={question.id}
                className={cn(
                  "flex items-start gap-2.5 rounded-lg border px-3 py-2",
                  correct ? "quiz-correct-surface" : "quiz-incorrect-surface",
                )}
              >
                {correct ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-quiz-correct" />
                ) : (
                  <XCircle className="mt-0.5 size-4 shrink-0 text-quiz-incorrect" />
                )}
                <span className="text-sm text-foreground/90">
                  <span className="mr-1.5 text-xs font-medium text-muted-foreground">
                    Q{i + 1}
                  </span>
                  {prompt}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
