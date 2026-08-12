"use client";

import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ArtifactMeta } from "@/lib/data-analysis/types";
import { ArtifactIcon } from "@/components/data-analysis/workspace/artifact-icon";
import { NewArtifactMenu } from "@/components/data-analysis/explorer/new-artifact-menu";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

/**
 * Open-document switcher, living in the app bar rather than on a row of its
 * own.
 *
 * The workspace holds one workbook plus a couple of PDFs, so a full IDE tab
 * strip cost a whole row of vertical space to show two or three chips.
 * Worksheets are *not* listed here — those are Univer's own tabs along the
 * bottom of the grid, which is where a spreadsheet user looks for them.
 */
export function DocumentSwitcher() {
  const { state, actions } = useWorkspace();

  const openArtifacts = state.openTabIds
    .map((id) => state.artifacts.find((artifact) => artifact.id === id))
    .filter((artifact): artifact is ArtifactMeta => Boolean(artifact));

  return (
    <div className="flex min-w-0 flex-1 items-center gap-1">
      <div
        role="tablist"
        aria-label="Open documents"
        className="scrollbar-thin flex min-w-0 items-center gap-1 overflow-x-auto"
      >
        {openArtifacts.map((artifact) => (
          <DocumentChip
            key={artifact.id}
            artifact={artifact}
            isActive={artifact.id === state.activeTabId}
            onActivate={() => actions.activateTab(artifact.id)}
            onClose={() => actions.closeTab(artifact.id)}
          />
        ))}
      </div>

      {/* No tooltip: the trigger already opens a labelled menu, and a tooltip
          around a menu trigger fights the popup for hover state. */}
      <NewArtifactMenu
        trigger={
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Add to workspace"
            title="Add to workspace"
            className="shrink-0"
          >
            <Plus />
          </Button>
        }
      />
    </div>
  );
}

interface DocumentChipProps {
  artifact: ArtifactMeta;
  isActive: boolean;
  onActivate: () => void;
  onClose: () => void;
}

function DocumentChip({
  artifact,
  isActive,
  onActivate,
  onClose,
}: DocumentChipProps) {
  return (
    <div
      className={cn(
        "group flex h-7 shrink-0 items-center rounded-md border transition-colors",
        isActive
          ? "border-border bg-muted text-foreground"
          : "border-transparent text-muted-foreground hover:bg-muted/50 hover:text-foreground",
      )}
    >
      <button
        type="button"
        role="tab"
        aria-selected={isActive}
        onClick={onActivate}
        onAuxClick={(event) => {
          if (event.button === 1) onClose();
        }}
        title={artifact.name}
        className="flex min-w-0 max-w-40 items-center gap-1.5 rounded-md py-1 pl-2 pr-1 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <ArtifactIcon
          type={artifact.type}
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
        onClick={onClose}
        className={cn(
          "mr-1 rounded p-0.5 text-muted-foreground outline-none transition-opacity hover:bg-background hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50",
          isActive ? "opacity-70" : "opacity-0 group-hover:opacity-70",
        )}
      >
        <X className="size-3" />
      </button>
    </div>
  );
}
