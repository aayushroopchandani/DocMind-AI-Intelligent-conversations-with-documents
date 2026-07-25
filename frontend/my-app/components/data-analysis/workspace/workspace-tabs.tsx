"use client";

import { FileSpreadsheet, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ArtifactMeta } from "@/lib/data-analysis/types";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

/**
 * IDE-style artifact tab strip. Tabs represent files (spreadsheets today,
 * PDFs/charts/reports later) — not worksheets, which Univer renders at the
 * bottom of the grid.
 */
export function WorkspaceTabs() {
  const { state, actions } = useWorkspace();

  const openArtifacts = state.openTabIds
    .map((id) => state.artifacts.find((artifact) => artifact.id === id))
    .filter((artifact): artifact is ArtifactMeta => Boolean(artifact));

  return (
    <div className="flex h-10 shrink-0 items-end gap-1 border-b border-border bg-card/30 px-2">
      <div
        role="tablist"
        aria-label="Open documents"
        className="scrollbar-thin flex min-w-0 flex-1 items-end gap-1 overflow-x-auto"
      >
        {openArtifacts.map((artifact) => {
          const isActive = artifact.id === state.activeTabId;
          return (
            <div
              key={artifact.id}
              className={cn(
                "group relative flex h-8 shrink-0 items-center rounded-t-lg border border-b-0 transition-colors",
                isActive
                  ? "border-border bg-background text-foreground"
                  : "border-transparent bg-transparent text-muted-foreground hover:bg-muted/40 hover:text-foreground",
              )}
            >
              <button
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => actions.activateTab(artifact.id)}
                onAuxClick={(event) => {
                  if (event.button === 1) actions.closeTab(artifact.id);
                }}
                className="flex min-w-0 max-w-52 items-center gap-1.5 rounded-t-lg py-1.5 pl-2.5 pr-1 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              >
                <FileSpreadsheet
                  className={cn(
                    "size-3.5 shrink-0",
                    isActive && "text-[color:var(--accent-cyan)]",
                  )}
                />
                <span className="truncate">{artifact.name}</span>
                {artifact.isDirty ? (
                  <span
                    aria-label="Unsaved changes"
                    className="size-1.5 shrink-0 rounded-full bg-[color:var(--accent-cyan)]"
                  />
                ) : null}
              </button>
              <button
                type="button"
                aria-label={`Close ${artifact.name}`}
                onClick={(event) => {
                  event.stopPropagation();
                  actions.closeTab(artifact.id);
                }}
                className={cn(
                  "mr-1 rounded-md p-0.5 text-muted-foreground outline-none transition-opacity hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50",
                  isActive ? "opacity-70" : "opacity-0 group-hover:opacity-70",
                )}
              >
                <X className="size-3" />
              </button>
              {isActive ? (
                <span className="absolute inset-x-2 top-0 h-px bg-[color:var(--accent-cyan)]/70" />
              ) : null}
            </div>
          );
        })}
      </div>

      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="New blank spreadsheet"
              onClick={actions.createSpreadsheet}
              className="mb-0.5 shrink-0"
            >
              <Plus />
            </Button>
          }
        />
        <TooltipContent>New blank spreadsheet</TooltipContent>
      </Tooltip>
    </div>
  );
}
