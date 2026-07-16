"use client";

import { CircleDotDashed, RotateCcw, Target, Zap } from "lucide-react";
import type { QuizAnswer, QuizAttempt } from "@/lib/quiz/types";
import { useCountUp } from "@/lib/quiz/use-count-up";
import { Button } from "@/components/ui/button";
import { QuestionReview } from "@/components/quiz/shared/question-review";

interface RapidFireResultProps {
  answers: QuizAnswer[];
  attempt: QuizAttempt;
  onRestart: () => void;
}

export function RapidFireResult({
  answers,
  attempt,
  onRestart,
}: RapidFireResultProps) {
  const result = attempt.result;
  const animatedScore = useCountUp(Math.round(result?.percentage ?? 0), 900);
  if (!result) return null;
  const evaluatedById = new Map(
    attempt.answers.map((answer) => [answer.question_id, answer.evaluation]),
  );

  return (
    <div className="animate-quiz-in space-y-6">
      <div className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="aurora-panel px-6 py-8 text-center">
          <div className="flex items-center justify-center gap-2 text-[color:var(--accent-amber)]">
            <Zap className="size-4" />
            <span className="text-xs font-semibold uppercase tracking-wide">Rapid fire complete</span>
          </div>
          <p className="mt-3 text-5xl font-bold tracking-tight text-foreground tabular-nums">
            {animatedScore}%
          </p>
          <p className="mt-1 text-sm text-muted-foreground">backend-evaluated score</p>
        </div>

        <div className="grid grid-cols-3 divide-x divide-border border-t border-border">
          <Stat icon={Target} label="Correct" value={String(result.correct)} />
          <Stat icon={CircleDotDashed} label="Partial" value={String(result.partially_correct)} />
          <Stat icon={Zap} label="Skipped" value={String(result.skipped)} />
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
          {attempt.review_questions.map((question, index) => {
            const evaluation = evaluatedById.get(question.id);
            return evaluation ? (
              <QuestionReview
                key={question.id}
                index={index}
                question={question}
                answer={answers[index]}
                evaluation={evaluation}
              />
            ) : null;
          })}
        </div>
      </div>
    </div>
  );
}

function Stat({ icon: Icon, label, value }: { icon: typeof Target; label: string; value: string }) {
  return (
    <div className="flex flex-col items-center gap-1 px-2 py-4">
      <Icon className="size-4 text-muted-foreground" />
      <span className="text-lg font-bold text-foreground tabular-nums">{value}</span>
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
    </div>
  );
}
