import { ArrowRight, ChevronDown } from "lucide-react";
import type { MatchTheFollowingQuestion } from "@/lib/quiz/types";
import { cn } from "@/lib/utils";

interface Props {
  question: MatchTheFollowingQuestion;
  pairs: Record<string, string>;
  submitted: boolean;
  parts?: Record<string, boolean>;
  onChange: (leftId: string, rightId: string) => void;
}

export function MatchTheFollowing({
  question,
  pairs,
  submitted,
  parts,
  onChange,
}: Props) {
  const rightById = new Map(question.right_items.map((r) => [r.id, r.text]));
  const correctByLeft = new Map(
    question.correct_matches.map((m) => [m.left_id, m.right_id]),
  );

  return (
    <div className="space-y-2.5">
      {question.left_items.map((left, i) => {
        const isCorrect = submitted ? parts?.[left.id] : undefined;
        return (
          <div
            key={left.id}
            style={{ "--i": i } as React.CSSProperties}
            className={cn(
              "animate-option-in flex items-center gap-3 rounded-xl border p-2.5",
              !submitted && "border-border bg-card",
              submitted && isCorrect && "quiz-correct-surface",
              submitted && isCorrect === false && "quiz-incorrect-surface",
            )}
          >
            <span className="flex-1 px-1.5 text-sm font-medium text-foreground">
              {left.text}
            </span>
            <ArrowRight className="size-4 shrink-0 text-muted-foreground" />

            <div className="relative w-1/2 shrink-0">
              <select
                aria-label={`Match for ${left.text}`}
                value={pairs[left.id] ?? ""}
                disabled={submitted}
                onChange={(e) => onChange(left.id, e.target.value)}
                className={cn(
                  "w-full appearance-none rounded-lg border border-border bg-background py-2 pl-3 pr-8 text-sm text-foreground outline-none transition-colors",
                  "focus:border-[color:var(--accent-cyan)]",
                  submitted && "cursor-default opacity-90",
                )}
              >
                <option value="" disabled>
                  Choose…
                </option>
                {question.right_items.map((right) => (
                  <option key={right.id} value={right.id}>
                    {right.text}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            </div>
          </div>
        );
      })}

      {submitted ? (
        <div className="space-y-1 pt-1">
          {question.left_items.map((left) =>
            parts?.[left.id] ? null : (
              <p key={left.id} className="text-xs text-muted-foreground">
                <span className="text-foreground">{left.text}</span> →{" "}
                <span className="font-medium text-quiz-correct">
                  {rightById.get(correctByLeft.get(left.id) ?? "")}
                </span>
              </p>
            ),
          )}
        </div>
      ) : null}
    </div>
  );
}
