import type { FillInTheBlankQuestion } from "@/lib/quiz/types";
import { cn } from "@/lib/utils";

interface Props {
  question: FillInTheBlankQuestion;
  values: Record<string, string>;
  onChange: (blankId: string, value: string) => void;
}

export function FillInTheBlank({
  question,
  values,
  onChange,
}: Props) {
  return (
    <div className="space-y-4">
      <p className="text-base leading-relaxed text-foreground">
        {question.question}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {question.blanks.map((blank, index) => (
          <label
            key={blank.blank_id}
            className="text-xs font-medium text-muted-foreground"
          >
            Blank {index + 1}
            <input
              type="text"
              value={values[blank.blank_id] ?? ""}
              onChange={(event) =>
                onChange(blank.blank_id, event.target.value)
              }
              placeholder="Type your answer"
              className={cn(
                "mt-1.5 w-full rounded-lg border bg-muted/50 px-3 py-2 text-sm font-medium text-foreground outline-none transition-colors",
                "focus:border-[color:var(--accent-cyan)] focus:bg-[color:var(--accent-cyan)]/10",
                "border-border",
              )}
            />
          </label>
        ))}
      </div>
    </div>
  );
}
