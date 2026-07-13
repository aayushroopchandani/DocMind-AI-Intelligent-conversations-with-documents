import { FileText } from "lucide-react";
import type { QuizCitation } from "@/lib/quiz/types";

interface CitationChipProps {
  citation: QuizCitation;
}

/**
 * Compact, read-only source reference shown under a question's explanation.
 * (Not clickable yet — wiring it to the PDF viewer is a backend-integration
 * concern handled alongside the real quiz payload.)
 */
export function CitationChip({ citation }: CitationChipProps) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-background/60 px-2.5 py-1.5">
      <FileText className="mt-0.5 size-3 shrink-0 text-[color:var(--accent-violet)]" />
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-[11px] font-medium text-foreground">
            {citation.document_name}
          </span>
          {citation.page_number ? (
            <span className="shrink-0 rounded-full border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              Page {citation.page_number}
            </span>
          ) : null}
        </div>
        {citation.excerpt ? (
          <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">
            &ldquo;{citation.excerpt}&rdquo;
          </p>
        ) : null}
      </div>
    </div>
  );
}
