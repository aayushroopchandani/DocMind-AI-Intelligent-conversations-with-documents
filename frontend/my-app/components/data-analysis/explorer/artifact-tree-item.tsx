"use client";

import {
  FolderOpen,
  PanelTopClose,
  Pencil,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import { formatFileSize } from "@/lib/data-analysis/pdf/pdf-validation";
import type { ArtifactMeta } from "@/lib/data-analysis/types";
import { isPdfArtifact } from "@/lib/data-analysis/types";
import { ArtifactIcon } from "@/components/data-analysis/workspace/artifact-icon";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

/**
 * One artifact row in the explorer: click opens/activates its workspace
 * tab, right-click offers file operations. Closing a tab never deletes the
 * artifact; deletion is confirmed via a dialog owned by the shell.
 */
export function ArtifactTreeItem({ artifact }: { artifact: ArtifactMeta }) {
  const { state, actions, ui } = useWorkspace();

  const isOpen = state.openTabIds.includes(artifact.id);
  const isActive = state.activeTabId === artifact.id;
  const pdf = isPdfArtifact(artifact) ? artifact.pdf : null;
  const isBroken = pdf?.loadingStatus === "missing" || pdf?.loadingStatus === "error";

  return (
    <ContextMenu>
      <ContextMenuTrigger
        render={
          <button
            type="button"
            onClick={() => actions.openArtifact(artifact.id)}
            title={
              pdf
                ? `${artifact.name} · ${formatFileSize(pdf.fileSize)}`
                : artifact.name
            }
            className={cn(
              "group flex w-full items-center gap-2 rounded-lg border border-transparent px-2 py-1.5 text-left text-sm outline-none transition-colors",
              "hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring/50",
              isActive
                ? "border-border bg-muted/60 text-foreground"
                : "text-muted-foreground",
            )}
          >
            <ArtifactIcon
              type={artifact.type}
              className={cn(
                "size-4 shrink-0",
                isActive
                  ? "text-[color:var(--accent-cyan)]"
                  : "text-muted-foreground group-hover:text-foreground",
              )}
            />
            <span className="min-w-0 flex-1 truncate">{artifact.name}</span>
            {isBroken ? (
              <TriangleAlert
                aria-label="This PDF is unavailable"
                className="size-3.5 shrink-0 text-destructive"
              />
            ) : artifact.isDirty ? (
              <span
                aria-label="Unsaved changes"
                className="size-1.5 shrink-0 rounded-full bg-[color:var(--accent-cyan)]"
              />
            ) : isOpen ? (
              <span
                aria-label="Open in workspace"
                className="size-1.5 shrink-0 rounded-full bg-muted-foreground/40"
              />
            ) : null}
          </button>
        }
      />
      <ContextMenuContent className="w-48">
        <ContextMenuItem onClick={() => actions.openArtifact(artifact.id)}>
          <FolderOpen />
          Open
        </ContextMenuItem>
        <ContextMenuItem onClick={() => ui.setRenameTargetId(artifact.id)}>
          <Pencil />
          Rename
        </ContextMenuItem>
        {/* No "Duplicate": the workspace keeps a single workbook, so copying
            a spreadsheet is "Insert → Duplicate sheet" inside it instead. */}
        {isOpen ? (
          <ContextMenuItem onClick={() => actions.closeTab(artifact.id)}>
            <PanelTopClose />
            Close tab
          </ContextMenuItem>
        ) : null}
        <ContextMenuSeparator />
        <ContextMenuItem
          variant="destructive"
          onClick={() => ui.setDeleteTargetId(artifact.id)}
        >
          <Trash2 />
          Delete
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}
