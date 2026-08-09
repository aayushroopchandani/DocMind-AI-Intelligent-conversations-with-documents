"use client";

import { useState } from "react";
import { PanelRightClose, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AnalysisPrivacyMode, AnalystMode } from "@/lib/data-analysis/types";
import { activeArtifact } from "@/lib/data-analysis/workspace-state";
import { AnalystComposer } from "@/components/data-analysis/analyst/analyst-composer";
import { ProposedActionCard } from "@/components/data-analysis/analyst/proposed-action-card";
import { useWorkspace } from "@/components/data-analysis/workspace-provider";
import { cn } from "@/lib/utils";
import { useAnalysisRuns } from "@/components/data-analysis/analysis-run-provider";

const MODES: Array<{ value: AnalystMode; label: string }> = [
  { value: "ask", label: "Ask" },
  { value: "analyse", label: "Analyse" },
  { value: "edit", label: "Edit" },
];

const PRIVACY_MODES: Array<{
  value: AnalysisPrivacyMode;
  label: string;
  description: string;
}> = [
  {
    value: "standard",
    label: "Standard",
    description: "Send bounded, non-sensitive examples after redaction.",
  },
  {
    value: "schema_only",
    label: "Schema only",
    description: "Send column types and statistics without example values.",
  },
  {
    value: "local_only",
    label: "Local data",
    description: "Keep all dataset values out of external model payloads.",
  },
];

/**
 * Prompt starters, chosen by the active artifact type. UI only — tapping one
 * fills the composer; nothing is sent until the analyst backend exists.
 */
const SUGGESTIONS: Record<"spreadsheet" | "pdf" | "none", string[]> = {
  spreadsheet: [
    "Summarise this sheet",
    "Find missing values",
    "Create a formula",
    "Compare selected columns",
  ],
  pdf: [
    "Summarise this page",
    "Explain the selected text",
    "Find financial tables",
    "Extract this table",
  ],
  none: [
    "Summarise this workspace",
    "What can you analyse?",
  ],
};

const HEADINGS: Record<"spreadsheet" | "pdf" | "none", string> = {
  spreadsheet: "Ask questions about the active spreadsheet",
  pdf: "Ask questions about the active document",
  none: "Open a document to start analysing",
};

interface AiAnalystPanelProps {
  onCollapse?: () => void;
}

/**
 * Right panel for durable analysis runs, typed plans, approval, and privacy.
 */
export function AiAnalystPanel({ onCollapse }: AiAnalystPanelProps) {
  const { state, actions } = useWorkspace();
  const runs = useAnalysisRuns();
  const [draft, setDraft] = useState("");

  const activeType = activeArtifact(state)?.type;
  const surface =
    activeType === "pdf" || activeType === "spreadsheet" ? activeType : "none";

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
              {HEADINGS[surface]}
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {surface === "pdf"
                ? "The analyst reads the active document through the same durable analysis pipeline and preserves every run."
                : "The analyst snapshots your selection, validates a typed plan and asks before risky edits."}
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-1.5">
              {SUGGESTIONS[surface].map((suggestion) => (
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

          <fieldset className="rounded-lg border border-border/70 bg-card/40 p-2.5">
            <legend className="flex items-center gap-1.5 px-1 text-[11px] font-medium text-muted-foreground">
              <ShieldCheck className="size-3.5" />
              Dataset privacy
            </legend>
            <div className="mt-1 grid grid-cols-3 gap-1" role="radiogroup" aria-label="Dataset privacy mode">
              {PRIVACY_MODES.map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  role="radio"
                  aria-checked={state.analysisPrivacyMode === mode.value}
                  title={mode.description}
                  onClick={() => actions.setAnalysisPrivacyMode(mode.value)}
                  className={cn(
                    "rounded-md border px-1.5 py-1 text-[10px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/50",
                    state.analysisPrivacyMode === mode.value
                      ? "border-[color:var(--accent-cyan)]/50 bg-muted text-foreground"
                      : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
                  )}
                >
                  {mode.label}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">
              {PRIVACY_MODES.find((mode) => mode.value === state.analysisPrivacyMode)?.description}
            </p>
          </fieldset>

          {runs.activeRun ? (
            <div className="rounded-lg border border-border/70 bg-muted/20 p-2.5">
              <p className="line-clamp-2 text-xs font-medium text-foreground">{runs.activeRun.prompt}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {runs.activeRun.status} · {runs.activeRun.phase.replaceAll("_", " ")}
              </p>
            </div>
          ) : null}
          <ProposedActionCard
            run={runs.activeRun}
            plan={runs.activePlan}
            onApprove={runs.approvePlan}
            onReject={() => runs.rejectPlan("other")}
          />
        </div>
      </ScrollArea>

      <AnalystComposer
        draft={draft}
        onDraftChange={setDraft}
        onSubmit={runs.submit}
        pending={runs.submitting}
      />
    </div>
  );
}
