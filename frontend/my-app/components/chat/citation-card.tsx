"use client";

import { FileText } from "lucide-react";
import type { Citation } from "@/lib/types";
import { cn } from "@/lib/utils";

interface CitationCardProps {
  citation: Citation;
  /** Called with the cited page so the viewer can navigate to it. */
  onNavigate: (pageNumber: number) => void;
  className?: string;
}

/**
 * Clickable citation. Activating it jumps the PDF viewer to the cited page.
 */
export function CitationCard({ citation, onNavigate, className }: CitationCardProps) {
  return (
    <button
      type="button"
      onClick={() => onNavigate(citation.pageNumber)}
      className={cn(
        "group flex w-full items-start gap-2.5 rounded-lg border border-border bg-card px-3 py-2 text-left transition-colors hover:border-foreground/30 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      <FileText className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-xs font-medium text-foreground">
            {citation.documentName}
          </span>
          <span className="shrink-0 rounded-full border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            Page {citation.pageNumber}
          </span>
        </span>
        {citation.preview ? (
          <span className="mt-1 block truncate text-[11px] text-muted-foreground">
            {citation.preview}
          </span>
        ) : null}
      </span>
    </button>
  );
}
