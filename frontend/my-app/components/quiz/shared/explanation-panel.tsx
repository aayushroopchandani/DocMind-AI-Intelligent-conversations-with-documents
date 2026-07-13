import { CheckCircle2, Lightbulb, XCircle } from "lucide-react";
import type { QuizCitation } from "@/lib/quiz/types";
import { cn } from "@/lib/utils";
import { CitationChip } from "./citation-chip";

interface ExplanationPanelProps {
  correct: boolean;
  explanation?: string;
  citations?: QuizCitation[];
}

/**
 * Instant-feedback block shown after a practice question is checked:
 * verdict banner + short explanation + collapsible source citations.
 */
export function ExplanationPanel({
  correct,
  explanation,
  citations,
}: ExplanationPanelProps) {
  return (
    <div
      className={cn(
        "animate-explanation-in overflow-hidden rounded-xl border p-4",
        correct ? "quiz-correct-surface" : "quiz-incorrect-surface",
      )}
    >
      <div className="flex items-center gap-2">
        {correct ? (
          <CheckCircle2 className="size-4 text-quiz-correct" />
        ) : (
          <XCircle className="size-4 text-quiz-incorrect" />
        )}
        <span
          className={cn(
            "text-sm font-semibold",
            correct ? "text-quiz-correct" : "text-quiz-incorrect",
          )}
        >
          {correct ? "Correct" : "Not quite"}
        </span>
      </div>

      {explanation ? (
        <div className="mt-3 flex gap-2">
          <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-[color:var(--accent-amber)]" />
          <p className="text-[13px] leading-relaxed text-foreground/90">
            {explanation}
          </p>
        </div>
      ) : null}

      {citations && citations.length > 0 ? (
        <div className="mt-3 space-y-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Source
          </p>
          {citations.map((citation, i) => (
            <CitationChip key={citation.chunk_id ?? i} citation={citation} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
