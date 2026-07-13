import { Fragment } from "react";
import type { FillInTheBlankQuestion } from "@/lib/quiz/types";
import { cn } from "@/lib/utils";

interface Props {
  question: FillInTheBlankQuestion;
  values: Record<string, string>;
  submitted: boolean;
  /** Per-blank correctness, present once submitted. */
  parts?: Record<string, boolean>;
  onChange: (blankId: string, value: string) => void;
}

export function FillInTheBlank({
  question,
  values,
  submitted,
  parts,
  onChange,
}: Props) {
  // Text is authored with runs of underscores marking each blank, in order.
  const segments = question.question.split(/_{2,}/);

  return (
    <div className="space-y-4">
      <p className="text-base leading-loose text-foreground">
        {segments.map((segment, i) => {
          const blank = question.blanks[i];
          return (
            <Fragment key={i}>
              {segment}
              {blank ? (
                <input
                  type="text"
                  aria-label={`Blank ${i + 1}`}
                  value={values[blank.blank_id] ?? ""}
                  disabled={submitted}
                  onChange={(e) => onChange(blank.blank_id, e.target.value)}
                  placeholder="…"
                  className={cn(
                    "mx-1 inline-block min-w-[7rem] rounded-md border-b-2 bg-muted/50 px-2 py-0.5 text-center text-sm font-medium outline-none transition-colors",
                    "focus:border-[color:var(--accent-cyan)] focus:bg-[color:var(--accent-cyan)]/10",
                    !submitted && "border-border",
                    submitted && parts?.[blank.blank_id]
                      ? "border-[color:var(--quiz-correct)] text-quiz-correct"
                      : submitted
                        ? "border-[color:var(--quiz-incorrect)] text-quiz-incorrect"
                        : "",
                  )}
                />
              ) : null}
            </Fragment>
          );
        })}
      </p>

      {submitted ? (
        <div className="space-y-1">
          {question.blanks.map((blank, i) =>
            parts?.[blank.blank_id] ? null : (
              <p key={blank.blank_id} className="text-xs text-muted-foreground">
                Blank {i + 1} accepts:{" "}
                <span className="font-medium text-quiz-correct">
                  {blank.correct_answers.join(", ")}
                </span>
              </p>
            ),
          )}
        </div>
      ) : null}
    </div>
  );
}
