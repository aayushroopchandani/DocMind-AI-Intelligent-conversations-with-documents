"use client";

import { FileText } from "lucide-react";
import type { Citation, DocumentContribution } from "@/lib/types";
import { cn } from "@/lib/utils";

interface CitationCardProps {
  citation: Citation;
  /** Called with the full citation so the viewer can switch tab + page. */
  onNavigate: (citation: Citation) => void;
  className?: string;
}

/**
 * Clickable citation. Activating it switches the PDF viewer to the cited
 * document tab and jumps to the cited page.
 */
export function CitationCard({ citation, onNavigate, className }: CitationCardProps) {
  return (
    <button
      type="button"
      onClick={() => onNavigate(citation)}
      className={cn(
        "group flex w-full items-start gap-2.5 rounded-lg border border-border bg-card px-3 py-2 text-left transition-colors hover:border-[color:var(--accent-cyan)]/40 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      <span className="mt-0.5 shrink-0 rounded-md border border-[color:var(--accent-cyan)]/30 bg-[color:var(--accent-cyan)]/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-[color:var(--accent-cyan)]">
        {citation.citationId}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-xs font-medium text-foreground">
            {citation.documentName}
          </span>
          {citation.pageNumber ? (
            <span className="shrink-0 rounded-full border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              Page {citation.pageNumber}
            </span>
          ) : null}
        </span>
        {citation.excerpt ? (
          <span className="mt-1 block truncate text-[11px] text-muted-foreground">
            {citation.excerpt}
          </span>
        ) : null}
      </span>
    </button>
  );
}

interface CitationGroupProps {
  documentName: string;
  citations: Citation[];
  contribution?: DocumentContribution;
  onNavigate: (citation: Citation) => void;
}

/** Citations for one document, with its contribution summary as the header. */
export function CitationGroup({
  documentName,
  citations,
  contribution,
  onNavigate,
}: CitationGroupProps) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-start gap-1.5 px-0.5">
        <FileText className="mt-0.5 size-3 shrink-0 text-[color:var(--accent-violet)]" />
        <div className="min-w-0">
          <p className="truncate text-[11px] font-semibold text-foreground">
            {documentName}
          </p>
          {contribution?.contribution ? (
            <p className="text-[11px] leading-snug text-muted-foreground">
              {contribution.contribution}
            </p>
          ) : null}
        </div>
      </div>
      {citations.map((citation) => (
        <CitationCard
          key={citation.citationId}
          citation={citation}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  );
}
