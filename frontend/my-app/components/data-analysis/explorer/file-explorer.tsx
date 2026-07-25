"use client";

import { useMemo, useState } from "react";
import {
  Database,
  FilePlus2,
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
import { ArtifactTreeItem } from "@/components/data-analysis/explorer/artifact-tree-item";
import { NewArtifactMenu } from "@/components/data-analysis/explorer/new-artifact-menu";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";

interface FileExplorerProps {
  /** Rendered inside the desktop panel (shows collapse) or a mobile sheet. */
  onCollapse?: () => void;
}

/** Left panel: the workspace file tree grouped by artifact origin. */
export function FileExplorer({ onCollapse }: FileExplorerProps) {
  const { state, actions } = useWorkspace();
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);

  const normalizedQuery = query.trim().toLowerCase();
  const spreadsheets = useMemo(
    () =>
      state.artifacts.filter(
        (artifact) =>
          artifact.source !== "generated" &&
          (!normalizedQuery ||
            artifact.name.toLowerCase().includes(normalizedQuery)),
      ),
    [state.artifacts, normalizedQuery],
  );
  const generated = useMemo(
    () =>
      state.artifacts.filter(
        (artifact) =>
          artifact.source === "generated" &&
          (!normalizedQuery ||
            artifact.name.toLowerCase().includes(normalizedQuery)),
      ),
    [state.artifacts, normalizedQuery],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
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
            <Button variant="ghost" size="icon-xs" aria-label="New artifact">
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
          <section aria-label="Spreadsheets">
            <p className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
              Spreadsheets
            </p>
            {spreadsheets.length > 0 ? (
              <div className="flex flex-col gap-0.5">
                {spreadsheets.map((artifact) => (
                  <ArtifactTreeItem key={artifact.id} artifact={artifact} />
                ))}
              </div>
            ) : normalizedQuery ? (
              <p className="px-2 py-1.5 text-xs text-muted-foreground">
                No files match “{query.trim()}”.
              </p>
            ) : (
              <div className="mx-1 flex flex-col items-start gap-2 rounded-lg border border-dashed border-border/80 p-3 animate-in fade-in duration-300">
                <p className="text-xs leading-relaxed text-muted-foreground">
                  No spreadsheets yet. Create one to start analysing data.
                </p>
                <Button size="xs" onClick={actions.createSpreadsheet}>
                  <FilePlus2 data-icon="inline-start" />
                  New blank spreadsheet
                </Button>
              </div>
            )}
          </section>

          <section aria-label="Generated artifacts">
            <p className="flex items-center gap-1 px-2 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
              <Sparkles className="size-3" />
              Generated
            </p>
            {generated.length > 0 ? (
              <div className="flex flex-col gap-0.5">
                {generated.map((artifact) => (
                  <ArtifactTreeItem key={artifact.id} artifact={artifact} />
                ))}
              </div>
            ) : (
              <p className="px-2 py-1 text-xs text-muted-foreground/70">
                Agent outputs will appear here.
              </p>
            )}
          </section>

          <section aria-label="Data sources">
            <p className="flex items-center gap-1 px-2 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
              <Database className="size-3" />
              Data sources
            </p>
            <p className="px-2 py-1 text-xs text-muted-foreground/70">
              Reserved for a later milestone.
            </p>
          </section>
        </div>
      </ScrollArea>
    </div>
  );
}
