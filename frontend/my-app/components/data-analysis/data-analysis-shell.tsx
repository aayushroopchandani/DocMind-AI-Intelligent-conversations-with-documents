"use client";

import { useEffect } from "react";
import { getUniverBridge } from "@/lib/data-analysis/univer-bridge";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ActivityDetailsSheet } from "@/components/data-analysis/activity/activity-details-sheet";
import { AgentActivityBar } from "@/components/data-analysis/activity/agent-activity-bar";
import { DataAnalysisTopbar } from "@/components/data-analysis/data-analysis-topbar";
import { DeleteArtifactDialog } from "@/components/data-analysis/dialogs/delete-artifact-dialog";
import { RenameArtifactDialog } from "@/components/data-analysis/dialogs/rename-artifact-dialog";
import { RunHistorySheet } from "@/components/data-analysis/history/run-history-sheet";
import { WorkspacePanels } from "@/components/data-analysis/workspace-panels";
import { AnalysisRunProvider } from "@/components/data-analysis/analysis-run-provider";
import {
  useWorkspace,
  WorkspaceProvider,
} from "@/components/data-analysis/workspace-provider";

/** Entry point for /data-analysis: providers + full-viewport IDE layout. */
export function DataAnalysisShell() {
  return (
    <WorkspaceProvider>
      <AnalysisRunProvider>
        <TooltipProvider>
          <DataAnalysisLayout />
          <Toaster theme="dark" position="bottom-center" />
        </TooltipProvider>
      </AnalysisRunProvider>
    </WorkspaceProvider>
  );
}

function DataAnalysisLayout() {
  const { state } = useWorkspace();
  useUndoRedoShortcuts();

  // Until localStorage hydrates, paint a stable skeleton with the exact
  // final frame dimensions — no layout shift, no SSR/client mismatch.
  if (!state.hydrated) {
    return <WorkspaceSkeleton />;
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
      <DataAnalysisTopbar />
      <WorkspacePanels />
      <AgentActivityBar />

      <RenameArtifactDialog />
      <DeleteArtifactDialog />
      <RunHistorySheet />
      <ActivityDetailsSheet />
    </div>
  );
}

/**
 * Global Cmd/Ctrl+Z / Shift+Z / Y for the active workbook — but only when
 * focus is outside text inputs and outside Univer itself (both own their
 * native undo behaviour).
 */
function useUndoRedoShortcuts() {
  const { state, actions } = useWorkspace();
  const univerReady = state.univerReady;

  useEffect(() => {
    if (!univerReady) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      if (key !== "z" && key !== "y") return;

      const target = event.target as HTMLElement | null;
      const univerContainer = getUniverBridge().containerEl;
      if (
        target?.closest("input, textarea, [contenteditable='true']") ||
        (univerContainer && univerContainer.contains(target))
      ) {
        return;
      }

      event.preventDefault();
      if (key === "y" || (key === "z" && event.shiftKey)) {
        actions.redo();
      } else {
        actions.undo();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [actions, univerReady]);
}

function WorkspaceSkeleton() {
  return (
    <div
      aria-busy
      aria-label="Loading data analysis workspace"
      className="flex h-dvh flex-col overflow-hidden bg-background"
    >
      <div className="flex h-13 shrink-0 items-center gap-3 border-b border-border px-3">
        <Skeleton className="size-7 rounded-lg" />
        <Skeleton className="h-4 w-40" />
        <div className="flex-1" />
        <Skeleton className="h-6 w-44" />
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="hidden w-[260px] shrink-0 border-r border-border p-3 lg:block">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="mt-3 h-8 w-full" />
          <Skeleton className="mt-2 h-8 w-full" />
        </div>
        <div className="min-w-0 flex-1 p-3">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="mt-3 h-[calc(100%-2.75rem)] w-full" />
        </div>
        <div className="hidden w-[384px] shrink-0 border-l border-border p-3 lg:block">
          <Skeleton className="h-4 w-20" />
          <Skeleton className="mt-3 h-24 w-full" />
        </div>
      </div>
      <div className="h-8 shrink-0 border-t border-border" />
    </div>
  );
}
