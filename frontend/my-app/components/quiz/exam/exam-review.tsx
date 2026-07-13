import { RotateCcw, ShieldAlert } from "lucide-react";
import type { GradedResult, Quiz, QuizAnswer } from "@/lib/quiz/types";
import { Button } from "@/components/ui/button";
import { QuestionReview } from "@/components/quiz/shared/question-review";
import { cn } from "@/lib/utils";

interface ExamReviewProps {
  quiz: Quiz;
  answers: QuizAnswer[];
  results: GradedResult[];
  violations: number;
  autoSubmitted: boolean;
  onRetake: () => void;
}

/** Post-submission report. Professional and static — no celebratory animation. */
export function ExamReview({
  quiz,
  answers,
  results,
  violations,
  autoSubmitted,
  onRetake,
}: ExamReviewProps) {
  const total = quiz.questions.length;
  const correct = results.filter((r) => r.correct).length;
  const pct = Math.round((correct / total) * 100);
  const passed = pct >= 50;

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-border bg-card p-6 sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Exam submitted
        </p>
        <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-3">
          <div>
            <span className="text-4xl font-bold tabular-nums text-foreground">
              {pct}%
            </span>
            <span className="ml-2 text-sm text-muted-foreground">
              {correct}/{total} correct
            </span>
          </div>
          <span
            className={cn(
              "rounded-full border px-3 py-1 text-xs font-semibold",
              passed
                ? "border-[color:var(--quiz-correct)]/30 bg-[color:var(--quiz-correct)]/10 text-quiz-correct"
                : "border-[color:var(--quiz-incorrect)]/30 bg-[color:var(--quiz-incorrect)]/10 text-quiz-incorrect",
            )}
          >
            {passed ? "Passed" : "Below passing"}
          </span>
        </div>

        {(violations > 0 || autoSubmitted) && (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-[color:var(--accent-amber)]/30 bg-[color:var(--accent-amber)]/[0.07] px-3 py-2 text-[13px] text-muted-foreground">
            <ShieldAlert className="mt-0.5 size-4 shrink-0 text-[color:var(--accent-amber)]" />
            <span>
              {violations} proctoring violation{violations === 1 ? "" : "s"}{" "}
              recorded during this attempt
              {autoSubmitted ? "; the exam was auto-submitted." : "."}
            </span>
          </div>
        )}

        <Button onClick={onRetake} variant="outline" className="mt-5 h-9">
          <RotateCcw className="size-4" data-icon="inline-start" />
          Retake exam
        </Button>
      </div>

      <div>
        <p className="mb-3 px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Detailed review
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
