"use client";

import dynamic from "next/dynamic";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { ArtifactRenderer } from "@/components/data-analysis/workspace/artifact-renderer";
import { EmptyWorkspace } from "@/components/data-analysis/workspace/empty-workspace";
import { WorkspaceTabs } from "@/components/data-analysis/workspace/workspace-tabs";
import { UniverLoadingState } from "@/components/data-analysis/workspace/spreadsheet/univer-loading-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

// Univer is heavy and strictly browser-only: load it lazily on the client,
// and only once a spreadsheet actually opens.
const UniverHost = dynamic(
  () => import("@/components/data-analysis/workspace/spreadsheet/univer-host"),
  { ssr: false, loading: () => <UniverLoadingState /> },
);

/**
 * Centre column: artifact tab strip + the active artifact surface.
 *
 * The Univer host mounts while at least one spreadsheet tab is open and is
 * merely hidden when a non-spreadsheet artifact is active — switching tabs
 * swaps workbook units instead of re-booting the engine. Closing the last
 * spreadsheet tab unmounts (and disposes) the instance entirely.
 */
export function WorkspaceShell() {
  const { state } = useWorkspace();
  const active = activeArtifact(state);
  const activeIsSpreadsheet = active?.type === "spreadsheet";

  const hasSpreadsheetTabs = state.openTabIds.some((id) =>
    state.artifacts.some(
      (artifact) => artifact.id === id && artifact.type === "spreadsheet",
    ),
  );

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col bg-background">
      <WorkspaceTabs />
      <div className="relative min-h-0 min-w-0 flex-1">
        {hasSpreadsheetTabs ? (
          <div
            className={cn(
              "absolute inset-0",
              !activeIsSpreadsheet && "invisible pointer-events-none",
            )}
            aria-hidden={!activeIsSpreadsheet}
          >
            <UniverHost />
          </div>
        ) : null}

        {active ? <ArtifactRenderer artifact={active} /> : <EmptyWorkspace />}
      </div>
    </div>
  );
}
