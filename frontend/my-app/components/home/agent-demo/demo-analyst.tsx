"use client";

import { ArrowUp, Check, Quote, Sparkles, TrendingUp } from "lucide-react";
import { TypingText } from "@/components/home/lib/typing-text";
import {
  DEMO_QUERY,
  DEMO_STEPS,
  DEMO_TOP_MOVER,
  DEMO_TOTALS,
  Phase,
  deltaPct,
} from "@/components/home/agent-demo/demo-script";
import { cn } from "@/lib/utils";

interface DemoAnalystProps {
  phase: number;
  instant?: boolean;
}

/**
 * The AI Analyst side of the console: the query is typed into the composer,
 * lifts into a message bubble, streams its trace, and lands on an insight card
 * with a citation and an apply affordance.
 */
export function DemoAnalyst({ phase, instant = false }: DemoAnalystProps) {
  const submitted = phase >= Phase.Thinking;
  const totalDelta = deltaPct(DEMO_TOTALS.q3, DEMO_TOTALS.q4);
  const moverDelta = deltaPct(DEMO_TOP_MOVER.q3, DEMO_TOP_MOVER.q4);

  return (
    <div className="flex h-full flex-col bg-card/40">
      <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2.5">
        <Sparkles className="size-3.5 text-[var(--accent-violet)]" />
        <span className="text-[11px] font-medium text-foreground/80">
          AI Analyst
        </span>
        <span
          className={cn(
            "ml-auto flex items-center gap-1.5 text-[10px] transition-colors",
            phase >= Phase.Thinking && phase < Phase.Insight
              ? "text-[var(--accent-cyan)]"
              : "text-muted-foreground",
          )}
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              phase >= Phase.Thinking && phase < Phase.Insight
                ? "animate-pulse bg-[var(--accent-cyan)]"
                : "bg-muted-foreground/50",
            )}
          />
          {phase >= Phase.Thinking && phase < Phase.Insight
            ? "Working"
            : "Ready"}
        </span>
      </div>

      {/* `overflow-y-auto` rather than `hidden`: the panel is sized to fit the
          finished run, but a longer translation must scroll, never clip. */}
      <div className="scrollbar-thin min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3">
        {/* Submitted query */}
        {submitted && (
          <div className="animate-demo-in ml-auto max-w-[92%] rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-[11px] font-medium leading-snug text-primary-foreground">
            {DEMO_QUERY}
          </div>
        )}

        {/*
         * Agent trace. Once the result lands it collapses to a one-line
         * summary — true to how a finished run reads, and it frees the height
         * the insight card needs inside a fixed-size panel.
         */}
        {phase >= Phase.Thinking && phase < Phase.Insight && (
          <ul className="space-y-1.5">
            {DEMO_STEPS.map((step, i) => (
              <li
                key={step.label}
                className="animate-demo-in flex items-start gap-2 text-[10.5px] leading-snug"
                style={{ "--i": i } as React.CSSProperties}
              >
                <Check className="mt-[3px] size-3 shrink-0 text-[var(--accent-emerald)]" />
                <span className="text-muted-foreground">
                  <span className="font-medium text-foreground/75">
                    {step.label}
                  </span>{" "}
                  <span className="font-mono">{step.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        )}

        {phase >= Phase.Insight && (
          <div className="flex items-center gap-2 text-[10.5px] text-muted-foreground">
            <Check className="size-3 shrink-0 text-[var(--accent-emerald)]" />
            <span>
              {DEMO_STEPS.length} steps completed
              <span className="font-mono"> · 2.1s</span>
            </span>
          </div>
        )}

        {/* Result */}
        {phase >= Phase.Insight && (
          <div
            className="animate-demo-in rounded-xl border border-border bg-background/60 p-3"
            style={{ "--i": 0 } as React.CSSProperties}
          >
            <div className="mb-1.5 flex items-center gap-1.5">
              <TrendingUp className="size-3 text-[var(--accent-emerald)]" />
              <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent-emerald)]">
                +{totalDelta}% QoQ
              </span>
            </div>
            <p className="text-[11px] leading-relaxed text-foreground/80">
              Q4 revenue reached{" "}
              <span className="font-semibold text-foreground">
                ${(DEMO_TOTALS.q4 / 1000).toFixed(2)}M
              </span>
              , up {totalDelta}% on Q3. {DEMO_TOP_MOVER.region} led the gain at{" "}
              <span className="font-semibold text-foreground">
                +{moverDelta}%
              </span>
              .
            </p>

            <div className="mt-2.5 flex items-center gap-2 rounded-lg border border-border bg-card px-2 py-1.5">
              <Quote className="size-2.5 shrink-0 text-muted-foreground" />
              <span className="truncate font-mono text-[9.5px] text-muted-foreground">
                revenue.xlsx · Sheet1!A1:D6
              </span>
            </div>

            <div className="mt-2.5 flex gap-1.5">
              <span className="rounded-md bg-foreground px-2 py-1 text-[10px] font-medium text-background">
                Apply to workspace
              </span>
              <span className="rounded-md border border-border px-2 py-1 text-[10px] text-muted-foreground">
                Preview diff
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="border-t border-border/70 p-2.5">
        <div
          className="flex items-center gap-2 rounded-xl border bg-background/60 px-2.5 py-2 transition-colors"
          style={{
            borderColor:
              phase === Phase.Typing
                ? "color-mix(in oklch, var(--accent-cyan) 45%, var(--border))"
                : "var(--border)",
          }}
        >
          <span className="min-w-0 flex-1 truncate text-[11px]">
            {phase === Phase.Typing ? (
              <span className="text-foreground/85">
                <TypingText
                  text={DEMO_QUERY}
                  run={phase === Phase.Typing}
                  instant={instant}
                />
              </span>
            ) : (
              <span className="text-muted-foreground/60">
                Ask about revenue.xlsx…
              </span>
            )}
          </span>
          <span
            className={cn(
              "flex size-5 shrink-0 items-center justify-center rounded-lg transition-colors",
              phase === Phase.Typing
                ? "bg-foreground text-background"
                : "bg-muted text-muted-foreground",
            )}
          >
            <ArrowUp className="size-3" />
          </span>
        </div>
      </div>
    </div>
  );
}
