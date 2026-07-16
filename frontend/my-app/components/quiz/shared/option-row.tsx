import type { OptionKey } from "@/lib/quiz/types";
import { cn } from "@/lib/utils";

/** Input-only state; correctness is evaluated and rendered from the backend. */
export type OptionState = "idle" | "selected";

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
      aria-pressed={state === "selected"}
      onClick={onClick}
      style={{ "--i": index } as React.CSSProperties}
      className={cn(
        "animate-option-in group flex w-full items-center gap-3 rounded-xl border p-3.5 text-left transition-all duration-200",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        !disabled && "hover:border-foreground/25 hover:bg-accent",
        state === "idle" && "border-border bg-card",
        state === "selected" &&
          "border-[color:var(--accent-cyan)]/60 bg-[color:var(--accent-cyan)]/10 quiz-glow",
        disabled && "cursor-default",
      )}
    >
      <span
        className={cn(
          "flex size-7 shrink-0 items-center justify-center border text-xs font-semibold transition-colors",
          multi ? "rounded-md" : "rounded-full",
          state === "selected" &&
            "border-transparent bg-[color:var(--accent-cyan)] text-black",
          state === "idle" &&
            "border-border text-muted-foreground group-hover:text-foreground",
        )}
      >
        {optionKey}
      </span>

      <span className="text-sm leading-snug text-foreground">{text}</span>
    </button>
  );
}
