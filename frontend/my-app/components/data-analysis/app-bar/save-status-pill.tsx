"use client";

import type { SaveStatus } from "@/lib/data-analysis/types";
import { cn } from "@/lib/utils";

const SAVE_LABEL: Record<SaveStatus, string> = {
  draft: "Local draft",
  saving: "Saving…",
  saved: "Saved locally",
};

interface SaveStatusPillProps {
  status: SaveStatus;
  /** Any open document with unflushed edits. */
  hasUnsavedEdits: boolean;
}

/**
 * Persistence indicator. Below `lg` it collapses to the dot alone — the
 * colour still carries the state, and the label is restored by the tooltip
 * text on the element itself.
 */
export function SaveStatusPill({
  status,
  hasUnsavedEdits,
}: SaveStatusPillProps) {
  const label = SAVE_LABEL[status];

  return (
    <div
      role="status"
      aria-live="polite"
      title={label}
      className="flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-background/60 px-2 py-1"
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          status === "saving"
            ? "animate-pulse bg-[color:var(--accent-cyan)]"
            : hasUnsavedEdits
              ? "bg-[color:var(--accent-amber)]"
              : "bg-muted-foreground/50",
        )}
      />
      <span className="hidden text-xs text-muted-foreground lg:inline">
        {label}
      </span>
    </div>
  );
}
