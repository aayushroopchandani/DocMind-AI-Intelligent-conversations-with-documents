"use client";

import { useEffect } from "react";
import { getUniverBridge } from "@/lib/data-analysis/univer-bridge";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ActivityDetailsSheet } from "@/components/data-analysis/activity/activity-details-sheet";
import { AgentActivityBar } from "@/components/data-analysis/activity/agent-activity-bar";
import { DataAnalysisAppBar } from "@/components/data-analysis/app-bar/app-bar";
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
  useWorkspaceShortcuts();

  // Until localStorage hydrates, paint a stable skeleton with the exact
  // final frame dimensions — no layout shift, no SSR/client mismatch.
  if (!state.hydrated) {
    return <WorkspaceSkeleton />;
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
      <DataAnalysisAppBar />
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
 * Workspace-level keyboard shortcuts.
 *
 * Cmd/Ctrl+S flushes the local draft from anywhere, including inside the
 * grid — the browser's "save this page" dialog is never what someone means
 * in a spreadsheet.
 *
 * Undo and redo are narrower: they only apply when focus is outside text
 * inputs and outside Univer itself, since both own their native behaviour.
 */
function useWorkspaceShortcuts() {
  const { state, actions } = useWorkspace();
  const univerReady = state.univerReady;

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();

      if (key === "s") {
        event.preventDefault();
        actions.saveNow();
        return;
      }

      if (!univerReady) return;
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
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-2">
        <Skeleton className="size-6 rounded-md" />
        <Skeleton className="h-4 w-64" />
        <div className="flex-1" />
        <Skeleton className="h-6 w-40" />
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="hidden w-[260px] shrink-0 border-r border-border p-3 lg:block">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="mt-3 h-8 w-full" />
          <Skeleton className="mt-2 h-8 w-full" />
        </div>
        <div className="min-w-0 flex-1 p-3">
          <Skeleton className="h-8 w-full" />
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
