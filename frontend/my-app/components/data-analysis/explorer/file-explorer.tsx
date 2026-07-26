"use client";

import { useMemo, useState, type DragEvent } from "react";
import {
  Database,
  FileText,
  FileUp,
  PanelLeftClose,
  Plus,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { dragEventHasFiles } from "@/lib/data-analysis/pdf/pdf-validation";
import { usePdfUpload } from "@/lib/data-analysis/use-pdf-upload";
import { ArtifactTreeItem } from "@/components/data-analysis/explorer/artifact-tree-item";
import { NewArtifactMenu } from "@/components/data-analysis/explorer/new-artifact-menu";
import { PdfUploadInput } from "@/components/data-analysis/explorer/pdf-upload-input";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

interface FileExplorerProps {
  /** Rendered inside the desktop panel (shows collapse) or a mobile sheet. */
  onCollapse?: () => void;
}

/** Left panel: the workspace file tree grouped by artifact kind. */
export function FileExplorer({ onCollapse }: FileExplorerProps) {
  const { state, actions } = useWorkspace();
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [isDropTarget, setIsDropTarget] = useState(false);
  const { inputRef, openFilePicker, addFiles, handleInputChange } =
    usePdfUpload();

  const normalizedQuery = query.trim().toLowerCase();

  const { spreadsheets, pdfs, generated } = useMemo(() => {
    const matches = state.artifacts.filter(
      (artifact) =>
        !normalizedQuery ||
        artifact.name.toLowerCase().includes(normalizedQuery),
    );
    return {
      spreadsheets: matches.filter(
        (artifact) =>
          artifact.type === "spreadsheet" && artifact.source !== "generated",
      ),
      pdfs: matches.filter((artifact) => artifact.type === "pdf"),
      generated: matches.filter((artifact) => artifact.source === "generated"),
    };
  }, [state.artifacts, normalizedQuery]);

  /* ---------------- drag-and-drop ---------------- */

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!dragEventHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDropTarget(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    // Ignore moves between child elements; only a real exit clears the state.
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setIsDropTarget(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!dragEventHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    setIsDropTarget(false);
    void addFiles(Array.from(event.dataTransfer.files));
  };

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        "relative flex h-full min-h-0 flex-col transition-colors",
        isDropTarget && "bg-[color:var(--accent-cyan)]/5",
      )}
    >
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-border px-3">
        <h2 className="min-w-0 flex-1 truncate text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Files
        </h2>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="Search files"
                aria-pressed={searchOpen}
                onClick={() => {
                  setSearchOpen((open) => !open);
                  if (searchOpen) setQuery("");
                }}
              >
                {searchOpen ? <X /> : <Search />}
              </Button>
            }
          />
          <TooltipContent>{searchOpen ? "Close search" : "Search files"}</TooltipContent>
        </Tooltip>
        <NewArtifactMenu
          trigger={
            <Button variant="ghost" size="icon-xs" aria-label="Add to workspace">
              <Plus />
            </Button>
          }
        />
        {onCollapse ? (
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label="Collapse file explorer"
                  onClick={onCollapse}
                >
                  <PanelLeftClose />
                </Button>
              }
            />
            <TooltipContent>Collapse</TooltipContent>
          </Tooltip>
        ) : null}
      </div>

      {searchOpen ? (
        <div className="shrink-0 border-b border-border p-2 animate-in fade-in slide-in-from-top-1 duration-200">
          <Input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter files…"
            aria-label="Filter files"
            className="h-7 text-xs"
          />
        </div>
      ) : null}

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-4 p-2">
          <ExplorerSection label="Spreadsheets">
            {spreadsheets.length > 0 ? (
              <ArtifactList artifacts={spreadsheets} />
            ) : normalizedQuery ? (
              <NoMatches query={query.trim()} />
            ) : (
              <EmptySectionHint
                text="No spreadsheets yet. Create one to start analysing data."
                actionLabel="New blank spreadsheet"
                onAction={actions.createSpreadsheet}
              />
            )}
          </ExplorerSection>

          <ExplorerSection label="PDF documents" icon={<FileText className="size-3" />}>
            {pdfs.length > 0 ? (
              <ArtifactList artifacts={pdfs} />
            ) : normalizedQuery ? (
              <NoMatches query={query.trim()} />
            ) : (
              <EmptySectionHint
                text="Drop PDFs here, or upload them to read and analyse in this workspace."
                actionLabel="Upload PDF"
                icon={<FileUp data-icon="inline-start" />}
                onAction={openFilePicker}
              />
            )}
          </ExplorerSection>

          <ExplorerSection label="Generated" icon={<Sparkles className="size-3" />}>
            {generated.length > 0 ? (
              <ArtifactList artifacts={generated} />
            ) : (
              <p className="px-2 py-1 text-xs text-muted-foreground/70">
                Agent outputs will appear here.
              </p>
            )}
          </ExplorerSection>

          <ExplorerSection label="Data sources" icon={<Database className="size-3" />}>
            <p className="px-2 py-1 text-xs text-muted-foreground/70">
              Reserved for a later milestone.
            </p>
          </ExplorerSection>
        </div>
      </ScrollArea>

      {isDropTarget ? (
        <div className="pointer-events-none absolute inset-1 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-[color:var(--accent-cyan)]/60 bg-background/80 animate-in fade-in duration-150">
          <p className="px-4 text-center text-xs font-medium text-foreground">
            Drop PDFs to add them to this workspace
          </p>
        </div>
      ) : null}

      <PdfUploadInput inputRef={inputRef} onChange={handleInputChange} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Section primitives                                                  */
/* ------------------------------------------------------------------ */

function ExplorerSection({
  label,
  icon,
  children,
}: {
  label: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section aria-label={label}>
      <p className="flex items-center gap-1 px-2 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
        {icon}
        {label}
      </p>
      {children}
    </section>
  );
}

function ArtifactList({
  artifacts,
}: {
  artifacts: React.ComponentProps<typeof ArtifactTreeItem>["artifact"][];
}) {
  return (
    <div className="flex flex-col gap-0.5">
      {artifacts.map((artifact) => (
        <ArtifactTreeItem key={artifact.id} artifact={artifact} />
      ))}
    </div>
  );
}

function NoMatches({ query }: { query: string }) {
  return (
    <p className="px-2 py-1.5 text-xs text-muted-foreground">
      No files match “{query}”.
    </p>
  );
}

function EmptySectionHint({
  text,
  actionLabel,
  onAction,
  icon,
}: {
  text: string;
  actionLabel: string;
  onAction: () => void;
  icon?: React.ReactNode;
}) {
  return (
    <div className="mx-1 flex flex-col items-start gap-2 rounded-lg border border-dashed border-border/80 p-3 animate-in fade-in duration-300">
      <p className="text-xs leading-relaxed text-muted-foreground">{text}</p>
      <Button size="xs" onClick={onAction}>
        {icon ?? <Plus data-icon="inline-start" />}
        {actionLabel}
      </Button>
    </div>
  );
}
