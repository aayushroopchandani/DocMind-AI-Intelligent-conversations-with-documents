import { ArrowRight, ChevronDown } from "lucide-react";
import type { MatchTheFollowingQuestion } from "@/lib/quiz/types";
import { cn } from "@/lib/utils";

interface Props {
  question: MatchTheFollowingQuestion;
  pairs: Record<string, string>;
  onChange: (leftId: string, rightId: string) => void;
}

export function MatchTheFollowing({
  question,
  pairs,
  onChange,
}: Props) {
  const selectedRightIds = new Set(Object.values(pairs));

  return (
    <div className="space-y-2.5">
      {question.left_items.map((left, i) => {
        return (
          <div
            key={left.id}
            style={{ "--i": i } as React.CSSProperties}
            className={cn(
              "animate-option-in flex items-center gap-3 rounded-xl border p-2.5",
              "border-border bg-card",
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
                onChange={(e) => onChange(left.id, e.target.value)}
                className={cn(
                  "w-full appearance-none rounded-lg border border-border bg-background py-2 pl-3 pr-8 text-sm text-foreground outline-none transition-colors",
                  "focus:border-[color:var(--accent-cyan)]",
                )}
              >
                <option value="" disabled>
                  Choose…
                </option>
                {question.right_items.map((right) => (
                  <option
                    key={right.id}
                    value={right.id}
                    disabled={
                      selectedRightIds.has(right.id) && pairs[left.id] !== right.id
                    }
                  >
                    {right.text}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            </div>
          </div>
        );
      })}
    </div>
  );
}
