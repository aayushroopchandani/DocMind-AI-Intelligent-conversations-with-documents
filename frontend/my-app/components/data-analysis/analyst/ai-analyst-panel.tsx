"use client";

import { useState } from "react";
import { PanelRightClose, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AnalystMode } from "@/lib/data-analysis/types";
import { AnalystComposer } from "@/components/data-analysis/analyst/analyst-composer";
import { ProposedActionCard } from "@/components/data-analysis/analyst/proposed-action-card";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";

const MODES: Array<{ value: AnalystMode; label: string }> = [
  { value: "ask", label: "Ask" },
  { value: "analyse", label: "Analyse" },
  { value: "edit", label: "Edit" },
];

const SUGGESTIONS = [
  "Summarise this sheet",
  "Find missing values",
  "Create a formula",
  "Compare selected columns",
];

interface AiAnalystPanelProps {
  onCollapse?: () => void;
}

/**
 * Right panel: the AI analyst shell. Fully client-side in this milestone —
 * no requests leave the browser and no analysis output is fabricated.
 */
export function AiAnalystPanel({ onCollapse }: AiAnalystPanelProps) {
  const { state, actions } = useWorkspace();
  const [draft, setDraft] = useState("");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
        <span className="ai-avatar inline-flex size-6 shrink-0 items-center justify-center rounded-md">
          <Sparkles className="size-3.5" />
        </span>
        <h2 className="min-w-0 flex-1 truncate text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          AI Analyst
        </h2>
        <div
          role="radiogroup"
          aria-label="Analyst mode"
          className="flex items-center rounded-lg border border-border bg-card/60 p-0.5"
        >
          {MODES.map((mode) => (
            <button
              key={mode.value}
              type="button"
              role="radio"
              aria-checked={state.analystMode === mode.value}
              onClick={() => actions.setAnalystMode(mode.value)}
              className={cn(
                "rounded-md px-2 py-0.5 text-[11px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50",
                state.analystMode === mode.value
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {mode.label}
            </button>
          ))}
        </div>
        {onCollapse ? (
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label="Collapse AI analyst"
                  onClick={onCollapse}
                >
                  <PanelRightClose />
                </Button>
              }
            />
            <TooltipContent>Collapse</TooltipContent>
          </Tooltip>
        ) : null}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-4 p-3">
          <div className="flex flex-col items-center px-2 pt-8 text-center animate-in fade-in duration-300">
            <span className="ai-avatar inline-flex size-10 items-center justify-center rounded-xl">
              <Sparkles className="size-5" />
            </span>
            <p className="mt-3 text-sm font-medium text-foreground">
              Ask questions about the active spreadsheet
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              The analyst will read your selection, propose edits and build
              new artifacts once its backend is connected.
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-1.5">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => setDraft(suggestion)}
                  className="rounded-full border border-border bg-card/60 px-2.5 py-1 text-xs text-muted-foreground outline-none transition-colors hover:border-[color:var(--accent-cyan)]/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>

          <ProposedActionCard />
        </div>
      </ScrollArea>

      <AnalystComposer draft={draft} onDraftChange={setDraft} />
    </div>
  );
}
