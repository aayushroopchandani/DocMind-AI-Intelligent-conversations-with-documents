"use client";

import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

interface SuggestedQuestionsProps {
  questions: readonly string[];
  onSelect: (question: string) => void;
  disabled?: boolean;
  className?: string;
}

/** Row of tappable prompt chips shown above the composer. */
export function SuggestedQuestions({
  questions,
  onSelect,
  disabled = false,
  className,
}: SuggestedQuestionsProps) {
  return (
    <div className={cn("flex flex-wrap gap-2", className)}>
      {questions.map((q) => (
        <button
          key={q}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(q)}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40"
        >
          <Sparkles className="size-3" />
          {q}
        </button>
      ))}
    </div>
  );
}
