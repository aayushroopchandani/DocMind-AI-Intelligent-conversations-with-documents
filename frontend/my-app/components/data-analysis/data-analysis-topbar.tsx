"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useUser } from "@clerk/nextjs";
import {
  BrainCircuit,
  Download,
  History,
  Import,
  PanelLeft,
  Redo2,
  Share2,
  Sparkles,
  Undo2,
} from "lucide-react";
import { AuthenticatedUserMenu } from "@/components/auth/authenticated-user-menu";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { notifyPendingFeature } from "@/lib/data-analysis/feedback";
import type { SaveStatus } from "@/lib/data-analysis/types";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

const SAVE_LABEL: Record<SaveStatus, string> = {
  draft: "Local draft",
  saving: "Saving…",
  saved: "Saved locally",
};

/** Compact application bar: brand, project identity, edit + file actions. */
export function DataAnalysisTopbar() {
  const { state, actions, ui } = useWorkspace();
  const { isSignedIn } = useUser();

  const artifact = activeArtifact(state);
  const canUndoRedo = state.univerReady && artifact?.type === "spreadsheet";
  const hasDirty = state.artifacts.some((item) => item.isDirty);

  return (
    <header className="flex h-13 shrink-0 items-center gap-2 border-b border-border bg-card/50 px-3 backdrop-blur">
      {/* Mobile: overlay panel triggers */}
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Open file explorer"
              className="lg:hidden"
              onClick={() => ui.setExplorerSheetOpen(true)}
            >
              <PanelLeft />
            </Button>
          }
        />
        <TooltipContent>Files</TooltipContent>
      </Tooltip>

      <div className="flex min-w-0 items-center gap-2">
        <Link
          href="/"
          className="flex shrink-0 items-center gap-2 text-sm font-semibold tracking-tight text-foreground"
        >
          <span className="ai-avatar inline-flex size-7 items-center justify-center rounded-lg">
            <BrainCircuit className="size-4" />
          </span>
          <span className="hidden md:inline">DocMind</span>
        </Link>
        <span className="hidden text-muted-foreground/50 md:inline">/</span>
        <span className="hidden shrink-0 text-sm text-muted-foreground md:inline">
          Data Analysis
        </span>
        <span className="text-muted-foreground/50">/</span>
        {/* Keyed so external changes (hydration) reset the draft cleanly. */}
        <ProjectNameInput
          key={state.project.name}
          name={state.project.name}
          onCommit={actions.setProjectName}
        />
      </div>

      <div
        className="flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-background/60 px-2.5 py-1"
        role="status"
        aria-live="polite"
      >
        <span
          aria-hidden
          className={cn(
            "size-1.5 rounded-full",
            state.saveStatus === "saving"
              ? "animate-pulse bg-[color:var(--accent-cyan)]"
              : hasDirty
                ? "bg-[color:var(--accent-amber)]"
                : "bg-muted-foreground/50",
          )}
        />
        <span className="text-xs text-muted-foreground">
          {SAVE_LABEL[state.saveStatus]}
        </span>
      </div>

      <div className="min-w-0 flex-1" />

      <div className="flex shrink-0 items-center gap-0.5">
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Undo"
                disabled={!canUndoRedo}
                onClick={actions.undo}
              >
                <Undo2 />
              </Button>
            }
          />
          <TooltipContent>Undo — ⌘Z</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Redo"
                disabled={!canUndoRedo}
                onClick={actions.redo}
              >
                <Redo2 />
              </Button>
            }
          />
          <TooltipContent>Redo — ⇧⌘Z</TooltipContent>
        </Tooltip>

        <Separator orientation="vertical" className="mx-1.5 h-5!" />

        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Run history"
                onClick={() => ui.setHistoryOpen(true)}
              >
                <History />
              </Button>
            }
          />
          <TooltipContent>Run history</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Import spreadsheet"
                onClick={() => notifyPendingFeature("import")}
              >
                <Import />
              </Button>
            }
          />
          <TooltipContent>Import — coming later</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Export spreadsheet"
                onClick={() => notifyPendingFeature("export")}
              >
                <Download />
              </Button>
            }
          />
          <TooltipContent>Export — coming later</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Share"
                onClick={() => notifyPendingFeature("share")}
              >
                <Share2 />
              </Button>
            }
          />
          <TooltipContent>Share — backend pending</TooltipContent>
        </Tooltip>

        {/* Mobile: analyst overlay trigger */}
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Open AI analyst"
                className="lg:hidden"
                onClick={() => ui.setAnalystSheetOpen(true)}
              >
                <Sparkles />
              </Button>
            }
          />
          <TooltipContent>AI Analyst</TooltipContent>
        </Tooltip>

        {isSignedIn ? (
          <div className="ml-1.5">
            <AuthenticatedUserMenu />
          </div>
        ) : null}
      </div>
    </header>
  );
}

/** Inline-editable project name that never fights the global undo hotkeys. */
function ProjectNameInput({
  name,
  onCommit,
}: {
  name: string;
  onCommit: (name: string) => void;
}) {
  const [value, setValue] = useState(name);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => onCommit(value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          onCommit(value);
          inputRef.current?.blur();
        }
        if (event.key === "Escape") {
          setValue(name);
          inputRef.current?.blur();
        }
      }}
      aria-label="Project name"
      spellCheck={false}
      className="h-7 w-36 min-w-0 truncate rounded-md border border-transparent bg-transparent px-1.5 text-sm font-medium text-foreground outline-none transition-colors hover:border-border focus-visible:border-border focus-visible:bg-card sm:w-44"
    />
  );
}
