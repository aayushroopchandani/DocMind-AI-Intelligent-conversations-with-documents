"use client";

import { BarChart3, FileSpreadsheet, FileText } from "lucide-react";
import { useSequence } from "@/components/home/lib/use-sequence";
import { DemoAnalyst } from "@/components/home/agent-demo/demo-analyst";
import { DemoChart } from "@/components/home/agent-demo/demo-chart";
import { DemoSheet } from "@/components/home/agent-demo/demo-sheet";
import {
  PHASE_DURATIONS,
  Phase,
} from "@/components/home/agent-demo/demo-script";
import { cn } from "@/lib/utils";

const TABS = [
  { icon: FileText, label: "Q4-report.pdf" },
  { icon: FileSpreadsheet, label: "revenue.xlsx" },
] as const;

/**
 * The homepage centrepiece: a looping, self-driving replica of the DocMind
 * analysis workspace.
 *
 * A single `useSequence` timer owns the phase; every child derives its state
 * from that number and animates with CSS. The loop pauses whenever the console
 * scrolls out of view and collapses to its finished frame under reduced motion.
 */
export function AgentConsole({ className }: { className?: string }) {
  const { ref, step, cycle, reduced } = useSequence<HTMLDivElement>({
    durations: PHASE_DURATIONS,
  });

  const charting = step >= Phase.Chart;

  return (
    <div
      ref={ref}
      className={cn(
        "glass overflow-hidden rounded-2xl shadow-2xl shadow-black/50",
        className,
      )}
    >
      {/* Window chrome + artifact tabs */}
      <div className="flex items-center gap-3 border-b border-border bg-background/40 px-3 py-2">
        <div className="flex shrink-0 gap-1.5">
          <span className="size-2.5 rounded-full bg-muted-foreground/30" />
          <span className="size-2.5 rounded-full bg-muted-foreground/20" />
          <span className="size-2.5 rounded-full bg-muted-foreground/10" />
        </div>

        <div className="scrollbar-thin flex min-w-0 items-center gap-1 overflow-hidden">
          {TABS.map((tab, i) => (
            <span
              key={tab.label}
              className={cn(
                "shrink-0 items-center gap-1.5 rounded-lg px-2 py-1 text-[10.5px] transition-colors",
                // The PDF tab is the first to go on narrow screens — the strip
                // would otherwise clip the chart tab the run creates.
                i === 0 ? "hidden sm:flex" : "flex",
                i === 1 && !charting
                  ? "bg-card text-foreground"
                  : "text-muted-foreground",
              )}
            >
              <tab.icon className="size-3" />
              {tab.label}
            </span>
          ))}

          {/* The chart tab is created by the run itself. */}
          <span
            className={cn(
              "flex shrink-0 items-center gap-1.5 rounded-lg px-2 py-1 text-[10.5px] transition-all duration-500",
              charting
                ? "translate-x-0 bg-card text-foreground opacity-100"
                : "-translate-x-2 opacity-0",
            )}
          >
            <BarChart3 className="size-3 text-[var(--accent-cyan)]" />
            revenue-by-region
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_290px]">
        {/* Artifact surface — the sheet cross-fades into the generated chart. */}
        <div className="relative h-[248px] border-b border-border bg-card/60 lg:h-[356px] lg:border-b-0 lg:border-r">
          <div
            className={cn(
              "absolute inset-0 transition-opacity duration-500",
              charting ? "pointer-events-none opacity-0" : "opacity-100",
            )}
          >
            <DemoSheet
              cycle={cycle}
              writingFormula={step >= Phase.Sheet}
              computed={step >= Phase.Sheet}
              instant={reduced}
            />
          </div>

          <div
            className={cn(
              "absolute inset-0 flex flex-col p-3 transition-opacity duration-500",
              charting ? "opacity-100" : "pointer-events-none opacity-0",
            )}
          >
            <div className="mb-1 flex items-baseline justify-between">
              <span className="text-[11px] font-medium text-foreground/85">
                Revenue by region
              </span>
              <span className="flex items-center gap-2.5 text-[9px] text-muted-foreground">
                <span className="flex items-center gap-1">
                  <span className="size-1.5 rounded-[2px] bg-muted" />
                  Q3
                </span>
                <span className="flex items-center gap-1">
                  <span className="size-1.5 rounded-[2px] bg-[var(--accent-cyan)]" />
                  Q4
                </span>
              </span>
            </div>
            <div className="min-h-0 flex-1">
              <DemoChart
                drawn={charting}
                showDeltas={step >= Phase.Insight}
              />
            </div>
          </div>
        </div>

        <div className="h-[352px] lg:h-[356px]">
          {/* The composer's typed line unmounts between phases, so it needs
              no cycle key — it starts fresh on every run by construction. */}
          <DemoAnalyst phase={step} instant={reduced} />
        </div>
      </div>
    </div>
  );
}
