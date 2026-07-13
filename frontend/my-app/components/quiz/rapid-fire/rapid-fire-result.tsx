"use client";

import { Flame, RotateCcw, Target, Zap } from "lucide-react";
import type { GradedResult, Quiz, QuizAnswer } from "@/lib/quiz/types";
import { useCountUp } from "@/lib/quiz/use-count-up";
import { Button } from "@/components/ui/button";
import { QuestionReview } from "@/components/quiz/shared/question-review";

interface RapidFireResultProps {
  quiz: Quiz;
  answers: QuizAnswer[];
  results: GradedResult[];
  points: number;
  bestStreak: number;
  onRestart: () => void;
}

export function RapidFireResult({
  quiz,
  answers,
  results,
  points,
  bestStreak,
  onRestart,
}: RapidFireResultProps) {
  const total = quiz.questions.length;
  const correct = results.filter((r) => r.correct).length;
  const accuracy = Math.round((correct / total) * 100);
  const animatedPoints = useCountUp(points, 1100);

  return (
    <div className="animate-quiz-in space-y-6">
      <div className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="aurora-panel px-6 py-8 text-center">
          <div className="flex items-center justify-center gap-2 text-[color:var(--accent-amber)]">
            <Zap className="size-4" />
            <span className="text-xs font-semibold uppercase tracking-wide">
              Rapid fire complete
            </span>
          </div>
          <p className="mt-3 text-5xl font-bold tracking-tight text-foreground tabular-nums">
            {animatedPoints.toLocaleString()}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">total points</p>
        </div>

        <div className="grid grid-cols-3 divide-x divide-border border-t border-border">
          <Stat icon={Target} label="Accuracy" value={`${accuracy}%`} />
          <Stat icon={Flame} label="Best streak" value={`${bestStreak}×`} />
          <Stat
            icon={Zap}
            label="Correct"
            value={`${correct}/${total}`}
          />
        </div>
      </div>

      <div className="flex justify-center">
        <Button onClick={onRestart} variant="outline" className="h-9">
          <RotateCcw className="size-4" data-icon="inline-start" />
          Play again
        </Button>
      </div>

      <div>
        <p className="mb-3 px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Review &amp; explanations
        </p>
        <div className="space-y-2.5">
          {quiz.questions.map((question, i) => (
            <QuestionReview
              key={question.id}
              index={i}
              question={question}
              answer={answers[i]}
              result={results[i]}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Target;
  label: string;
  value: string;
}) {
  return (
    <div className="flex flex-col items-center gap-1 px-2 py-4">
      <Icon className="size-4 text-muted-foreground" />
      <span className="text-lg font-bold text-foreground tabular-nums">
        {value}
      </span>
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
    </div>
  );
}
