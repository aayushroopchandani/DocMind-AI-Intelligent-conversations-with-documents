"use client";

import { ChevronUp, FileSpreadsheet } from "lucide-react";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import type { SaveStatus } from "@/lib/data-analysis/types";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";
import { useAnalysisRuns } from "@/components/data-analysis/analysis-run-provider";

/**
 * Bottom status bar. Today it only ever reports "Ready" — real run phases
 * (reading, cleaning, calculating, waiting for approval, …) arrive with the
 * agent backend and will reuse this same status-dot + label slot.
 */
export type AgentRunPhase =
  | "ready"
  | "reading"
  | "cleaning"
  | "calculating"
  | "generating-chart"
  | "waiting-approval"
  | "completed"
  | "failed";

const SAVE_LABEL: Record<SaveStatus, string> = {
  draft: "Local draft",
  saving: "Saving…",
  saved: "Saved locally",
};

export function AgentActivityBar() {
  const { state, ui } = useWorkspace();
  const { activeRun } = useAnalysisRuns();
  const artifact = activeArtifact(state);
  const statusLabel = activeRun
    ? activeRun.status === "waiting"
      ? "Waiting for approval"
      : activeRun.status === "paused"
        ? "Paused"
        : activeRun.phase.replaceAll("_", " ")
    : "Ready";
  const terminal = activeRun && ["succeeded", "failed", "cancelled", "expired"].includes(activeRun.status);

  return (
    <button
      type="button"
      onClick={() => ui.setActivityOpen(true)}
      aria-label="Open agent activity details"
      className="flex h-8 w-full shrink-0 items-center gap-3 border-t border-border bg-card/40 px-3 text-left outline-none transition-colors hover:bg-card/70 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50"
    >
      <span className="flex items-center gap-1.5">
        <span
          aria-hidden
          className={cn(
            "size-2 rounded-full",
            activeRun?.status === "failed" ? "bg-destructive" : "bg-[color:var(--accent-cyan)]",
            activeRun && !terminal && activeRun.status !== "paused" && "animate-pulse shadow-[0_0_6px_var(--accent-cyan)]",
          )}
        />
        <span className="text-xs font-medium capitalize text-foreground">{statusLabel}</span>
      </span>
      <span className="max-w-72 truncate text-xs text-muted-foreground">
        {activeRun ? activeRun.prompt : "No active agent run"}
      </span>

      <span className="min-w-0 flex-1" />

      {artifact ? (
        <span className="hidden min-w-0 items-center gap-1.5 text-xs text-muted-foreground sm:flex">
          <FileSpreadsheet className="size-3 shrink-0" />
          <span className="max-w-44 truncate">{artifact.name}</span>
          {state.analystContext.worksheetName ? (
            <>
              <span className="text-muted-foreground/40">·</span>
              <span className="truncate">
                {state.analystContext.worksheetName}
              </span>
            </>
          ) : null}
        </span>
      ) : null}

      <span
        className={cn(
          "text-xs tabular-nums",
          state.saveStatus === "saving"
            ? "text-[color:var(--accent-cyan)]"
            : "text-muted-foreground",
        )}
      >
        {SAVE_LABEL[state.saveStatus]}
      </span>
      <ChevronUp className="size-3.5 text-muted-foreground" />
    </button>
  );
}
