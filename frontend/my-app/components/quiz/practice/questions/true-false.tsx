import { Check, X } from "lucide-react";
import type { TrueFalseQuestion } from "@/lib/quiz/types";
import { cn } from "@/lib/utils";

interface Props {
  question: TrueFalseQuestion;
  value: boolean | null;
  submitted: boolean;
  onSelect: (value: boolean) => void;
}

export function TrueFalse({ question, value, submitted, onSelect }: Props) {
  function stateClass(choice: boolean): string {
    if (!submitted) {
      return value === choice
        ? "border-[color:var(--accent-cyan)]/60 bg-[color:var(--accent-cyan)]/10 quiz-glow"
        : "border-border bg-card hover:border-foreground/25 hover:bg-accent";
    }
    const isCorrect = choice === question.correct_answer;
    if (isCorrect) return "quiz-correct-surface animate-quiz-pop";
    if (value === choice) return "quiz-incorrect-surface animate-quiz-shake";
    return "border-border bg-card opacity-60";
  }

  const choices: { label: string; value: boolean; icon: typeof Check }[] = [
    { label: "True", value: true, icon: Check },
    { label: "False", value: false, icon: X },
  ];

  return (
    <div className="grid grid-cols-2 gap-3">
      {choices.map(({ label, value: choice, icon: Icon }, i) => (
        <button
          key={label}
          type="button"
          disabled={submitted}
          onClick={() => onSelect(choice)}
          style={{ "--i": i } as React.CSSProperties}
          className={cn(
            "animate-option-in flex flex-col items-center justify-center gap-2 rounded-xl border py-7 transition-all duration-200",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            stateClass(choice),
          )}
        >
          <Icon className="size-6" />
          <span className="text-sm font-semibold">{label}</span>
        </button>
      ))}
    </div>
  );
}
