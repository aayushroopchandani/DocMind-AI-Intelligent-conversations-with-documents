"use client";

import { useRef } from "react";
import { FileText, Loader2, Plus, X, AlertCircle, CheckCircle2 } from "lucide-react";
import type { PdfDoc } from "@/lib/types";
import { cn } from "@/lib/utils";
import { formatBytes } from "@/lib/pdf";

interface PdfTabsProps {
  docs: PdfDoc[];
  activeId: string | null;
  maxFiles: number;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
  /** Called with newly picked files from the inline "Add PDF" control. */
  onAddFiles: (files: File[]) => void;
}

function statusLabel(doc: PdfDoc): string {
  if (doc.status === "uploading") return "Uploading…";
  if (doc.status === "error") return doc.error ?? "Failed";
  const pages = doc.numPages || doc.cloudinaryPages;
  return pages ? `Ready • ${pages} pages` : "Ready";
}

/**
 * Horizontal, scrollable document tabs shown above the PDF workspace. Only one
 * tab is active at a time; each shows filename, status, and a remove control.
 */
export function PdfTabs({
  docs,
  activeId,
  maxFiles,
  onSelect,
  onRemove,
  onAddFiles,
}: PdfTabsProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const canAdd = docs.length < maxFiles;

  return (
    <div className="flex items-center gap-2 border-b border-border bg-card/50 px-2 py-2">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <span className="hidden rounded-md bg-background/60 px-2 py-1 tabular-nums sm:inline">
          Documents: {docs.length} / {maxFiles}
        </span>
      </div>

      <div className="scrollbar-thin flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
        {docs.map((doc) => {
          const active = doc.id === activeId;
          return (
            <div
              key={doc.id}
              className={cn(
                "group flex shrink-0 items-center gap-2 rounded-xl border px-2.5 py-1.5 text-left transition-all",
                active
                  ? "border-foreground/40 bg-background shadow-sm"
                  : "border-border bg-card/40 hover:border-foreground/20 hover:bg-card/70",
              )}
            >
              <button
                type="button"
                onClick={() => onSelect(doc.id)}
                title={doc.name}
                className="flex min-w-0 items-center gap-2 focus-visible:outline-none"
              >
                <span className="shrink-0 text-muted-foreground">
                  {doc.status === "uploading" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : doc.status === "error" ? (
                    <AlertCircle className="size-4 text-destructive" />
                  ) : active ? (
                    <CheckCircle2 className="size-4 text-foreground" />
                  ) : (
                    <FileText className="size-4" />
                  )}
                </span>
                <span className="flex min-w-0 flex-col">
                  <span
                    className={cn(
                      "max-w-[9rem] truncate text-xs font-medium",
                      active ? "text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {doc.name}
                  </span>
                  <span
                    className={cn(
                      "truncate text-[10px]",
                      doc.status === "error"
                        ? "text-destructive"
                        : "text-muted-foreground/70",
                    )}
                  >
                    {doc.sizeBytes ? `${formatBytes(doc.sizeBytes)} · ` : ""}
                    {statusLabel(doc)}
                  </span>
                </span>
              </button>

              <button
                type="button"
                onClick={() => onRemove(doc.id)}
                aria-label={`Remove ${doc.name}`}
                title="Remove"
                className="shrink-0 rounded-md p-1 text-muted-foreground/60 transition-colors hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}

        {canAdd ? (
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="flex shrink-0 items-center gap-1.5 rounded-xl border border-dashed border-border px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus className="size-4" />
            Add PDF
          </button>
        ) : null}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        className="sr-only"
        onChange={(e) => {
          if (e.target.files) onAddFiles(Array.from(e.target.files));
          e.target.value = "";
        }}
      />
    </div>
  );
}
