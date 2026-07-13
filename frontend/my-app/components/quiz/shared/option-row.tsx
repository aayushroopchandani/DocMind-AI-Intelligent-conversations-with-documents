import { Check, X } from "lucide-react";
import type { OptionKey } from "@/lib/quiz/types";
import { cn } from "@/lib/utils";

/**
 * Visual state of a single option.
 * - `idle`      : not selected, quiz still answerable
 * - `selected`  : chosen by the learner, not yet checked
 * - `correct`   : revealed as a right answer (after checking)
 * - `incorrect` : the learner picked this but it is wrong
 * - `missed`    : a right answer the learner failed to pick
 */
export type OptionState =
  | "idle"
  | "selected"
  | "correct"
  | "incorrect"
  | "missed";

interface OptionRowProps {
  optionKey: OptionKey;
  text: string;
  state: OptionState;
  /** Checkbox (multi-select) vs radio (single-select) affordance. */
  multi?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  /** Stagger index for the entrance animation. */
  index: number;
}

export function OptionRow({
  optionKey,
  text,
  state,
  multi = false,
  disabled = false,
  onClick,
  index,
}: OptionRowProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={{ "--i": index } as React.CSSProperties}
      className={cn(
        "animate-option-in group flex w-full items-center gap-3 rounded-xl border p-3.5 text-left transition-all duration-200",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        !disabled && "hover:border-foreground/25 hover:bg-accent",
        state === "idle" && "border-border bg-card",
        state === "selected" &&
          "border-[color:var(--accent-cyan)]/60 bg-[color:var(--accent-cyan)]/10 quiz-glow",
        state === "correct" && "quiz-correct-surface animate-quiz-pop",
        state === "incorrect" && "quiz-incorrect-surface animate-quiz-shake",
        state === "missed" &&
          "border-[color:var(--quiz-correct)]/40 border-dashed bg-transparent",
        disabled && "cursor-default",
      )}
    >
      <span
        className={cn(
          "flex size-7 shrink-0 items-center justify-center border text-xs font-semibold transition-colors",
          multi ? "rounded-md" : "rounded-full",
          state === "selected" &&
            "border-transparent bg-[color:var(--accent-cyan)] text-black",
          state === "correct" &&
            "border-transparent bg-[color:var(--quiz-correct)] text-black",
          state === "incorrect" &&
            "border-transparent bg-[color:var(--quiz-incorrect)] text-white",
          state === "missed" && "border-[color:var(--quiz-correct)]/60 text-quiz-correct",
          (state === "idle") && "border-border text-muted-foreground group-hover:text-foreground",
        )}
      >
        {state === "correct" || state === "missed" ? (
          <Check className="size-3.5" />
        ) : state === "incorrect" ? (
          <X className="size-3.5" />
        ) : (
          optionKey
        )}
      </span>

      <span className="text-sm leading-snug text-foreground">{text}</span>
    </button>
  );
}
