"use client";

import {
  CheckCircle2,
  CircleDashed,
  CircleDotDashed,
  RotateCcw,
  Trophy,
  XCircle,
} from "lucide-react";
import type { Quiz, QuizAnswerStatus, QuizAttempt } from "@/lib/quiz/types";
import { useCountUp } from "@/lib/quiz/use-count-up";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface QuizResultProps {
  quiz: Quiz;
  attempt: QuizAttempt;
  onRestart: () => void;
}

const STATUS_LABEL: Record<QuizAnswerStatus, string> = {
  correct: "Correct",
  incorrect: "Incorrect",
  partially_correct: "Partial credit",
  skipped: "Skipped",
};

export function QuizResult({ quiz, attempt, onRestart }: QuizResultProps) {
  const result = attempt.result;
  const pct = Math.round(result?.percentage ?? 0);
  const animatedPct = useCountUp(pct);
  if (!result) return null;
  const answerById = new Map(attempt.answers.map((answer) => [answer.question_id, answer]));
  const headline =
    pct >= 80 ? "Excellent work!" : pct >= 50 ? "Nice effort!" : "Keep practicing!";

  const radius = 52;
  const circumference = 2 * Math.PI * radius;

  return (
    <div className="animate-quiz-in rounded-2xl border border-border bg-card p-8 sm:p-10">
      <div className="flex flex-col items-center gap-6 sm:flex-row sm:gap-8">
        <div className="relative size-32 shrink-0">
          <svg viewBox="0 0 120 120" className="size-full -rotate-90">
            <circle cx="60" cy="60" r={radius} fill="none" stroke="var(--muted)" strokeWidth="8" />
            <circle
              cx="60"
              cy="60"
              r={radius}
              fill="none"
              stroke="url(#quiz-ring)"
              strokeWidth="8"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={circumference - (animatedPct / 100) * circumference}
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
            <span className="text-3xl font-bold text-foreground">{animatedPct}%</span>
            <span className="text-xs text-muted-foreground">
              {result.score}/{result.maximum_score} marks
            </span>
          </div>
        </div>

        <div className="text-center sm:text-left">
          <div className="flex items-center justify-center gap-2 text-[color:var(--accent-amber)] sm:justify-start">
            <Trophy className="size-4" />
            <span className="text-xs font-medium uppercase tracking-wide">Practice complete</span>
          </div>
          <h2 className="mt-2 text-2xl font-semibold text-foreground">{headline}</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {result.correct} correct · {result.partially_correct} partial · {result.incorrect} incorrect · {result.skipped} skipped
          </p>
          <Button onClick={onRestart} variant="outline" className="mt-4 h-9">
            <RotateCcw className="size-4" data-icon="inline-start" />
            Retry quiz
          </Button>
        </div>
      </div>

      <div className="mt-8 border-t border-border pt-6">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Backend-evaluated review
        </p>
        <ul className="space-y-1.5">
          {quiz.questions.map((question, index) => {
            const status =
              answerById.get(question.id)?.evaluation?.status ?? "skipped";
            const prompt = question.type === "true_false" ? question.statement : question.question;
            const Icon =
              status === "correct"
                ? CheckCircle2
                : status === "incorrect"
                  ? XCircle
                  : status === "partially_correct"
                    ? CircleDotDashed
                    : CircleDashed;
            return (
              <li
                key={question.id}
                className={cn(
                  "flex items-start gap-2.5 rounded-lg border px-3 py-2",
                  status === "correct" && "quiz-correct-surface",
                  status === "incorrect" && "quiz-incorrect-surface",
                  status === "partially_correct" && "border-[color:var(--accent-amber)]/35 bg-[color:var(--accent-amber)]/[0.07]",
                  status === "skipped" && "border-border bg-muted/30",
                )}
              >
                <Icon
                  className={cn(
                    "mt-0.5 size-4 shrink-0",
                    status === "correct" && "text-quiz-correct",
                    status === "incorrect" && "text-quiz-incorrect",
                    status === "partially_correct" && "text-[color:var(--accent-amber)]",
                    status === "skipped" && "text-muted-foreground",
                  )}
                />
                <span className="min-w-0 flex-1 text-sm text-foreground/90">
                  <span className="mr-1.5 text-xs font-medium text-muted-foreground">Q{index + 1}</span>
                  {prompt}
                </span>
                <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
                  {STATUS_LABEL[status]}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
