import { CheckCircle2, Lightbulb, XCircle } from "lucide-react";
import type { GradedResult, QuizAnswer, QuizQuestion } from "@/lib/quiz/types";
import { describeAnswer, describeCorrect } from "@/lib/quiz/describe";
import { cn } from "@/lib/utils";
import { CitationChip } from "./citation-chip";

interface QuestionReviewProps {
  index: number;
  question: QuizQuestion;
  answer: QuizAnswer;
  result: GradedResult;
}

/**
 * Read-only per-question breakdown shown on completion screens: prompt,
 * verdict, the learner's answer vs. the correct one, explanation and sources.
 */
export function QuestionReview({
  index,
  question,
  answer,
  result,
}: QuestionReviewProps) {
  const prompt =
    question.type === "true_false" ? question.statement : question.question;
  const correct = result.correct;

  return (
    <div
      className={cn(
        "rounded-xl border p-4",
        correct ? "quiz-correct-surface" : "quiz-incorrect-surface",
      )}
    >
      <div className="flex items-start gap-2.5">
        {correct ? (
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-quiz-correct" />
        ) : (
          <XCircle className="mt-0.5 size-4 shrink-0 text-quiz-incorrect" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">
            <span className="mr-1.5 text-xs font-semibold text-muted-foreground">
              Q{index + 1}
            </span>
            {prompt}
          </p>

          <dl className="mt-3 space-y-1.5 text-[13px]">
            <div className="flex gap-2">
              <dt className="w-24 shrink-0 text-muted-foreground">
                Your answer
              </dt>
              <dd
                className={cn(
                  "min-w-0 font-medium",
                  correct ? "text-quiz-correct" : "text-quiz-incorrect",
                )}
              >
                {describeAnswer(question, answer)}
              </dd>
            </div>
            {!correct ? (
              <div className="flex gap-2">
                <dt className="w-24 shrink-0 text-muted-foreground">Correct</dt>
                <dd className="min-w-0 font-medium text-quiz-correct">
                  {describeCorrect(question)}
                </dd>
              </div>
            ) : null}
          </dl>

          {question.short_explanation ? (
            <div className="mt-3 flex gap-2">
              <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-[color:var(--accent-amber)]" />
              <p className="text-[13px] leading-relaxed text-foreground/85">
                {question.short_explanation}
              </p>
            </div>
          ) : null}

          {question.citations && question.citations.length > 0 ? (
            <div className="mt-3 space-y-1.5">
              {question.citations.map((citation, i) => (
                <CitationChip
                  key={citation.chunk_id ?? i}
                  citation={citation}
                />
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
