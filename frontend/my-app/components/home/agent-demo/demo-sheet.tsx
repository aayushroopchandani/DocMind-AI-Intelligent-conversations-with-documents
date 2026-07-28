"use client";

import { Sigma } from "lucide-react";
import { TypingText } from "@/components/home/lib/typing-text";
import {
  DEMO_FORMULA,
  DEMO_ROWS,
  DEMO_TOTALS,
  deltaPct,
} from "@/components/home/agent-demo/demo-script";
import { cn } from "@/lib/utils";

interface DemoSheetProps {
  /** Loop counter — remounts the formula line so it retypes each run. */
  cycle: number;
  /** Type the formula into the formula bar. */
  writingFormula: boolean;
  /** Reveal the computed delta column. */
  computed: boolean;
  /** Render finished state with no typing (reduced motion). */
  instant?: boolean;
}

const COLUMNS = ["A", "B", "C", "D"] as const;
const HEADERS = ["Region", "Q3", "Q4", "Δ %"] as const;

/**
 * A miniature spreadsheet the agent writes into — the artifact side of the
 * demo. The delta column starts empty and is filled by the run, which is the
 * whole point: DocMind edits the sheet rather than describing it.
 */
export function DemoSheet({
  cycle,
  writingFormula,
  computed,
  instant = false,
}: DemoSheetProps) {
  return (
    <div className="flex h-full flex-col">
      {/* Formula bar */}
      <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2">
        <span className="rounded border border-border bg-background/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          D2
        </span>
        <Sigma className="size-3 shrink-0 text-muted-foreground" />
        <code className="truncate font-mono text-[10px] text-[var(--accent-emerald)]">
          <TypingText
            key={cycle}
            text={DEMO_FORMULA}
            run={writingFormula}
            instant={instant}
            speed={22}
          />
        </code>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden p-2">
        <table className="w-full table-fixed border-collapse text-[10px]">
          <colgroup>
            <col className="w-[42%]" />
            <col className="w-[19%]" />
            <col className="w-[19%]" />
            <col className="w-[20%]" />
          </colgroup>

          <thead>
            <tr>
              <th className="w-5 border border-border/60 bg-muted/40 py-1" />
              {COLUMNS.map((col) => (
                <th
                  key={col}
                  className="border border-border/60 bg-muted/40 py-1 font-mono text-[9px] font-normal text-muted-foreground"
                >
                  {col}
                </th>
              ))}
            </tr>
            <tr>
              <td className="border border-border/60 bg-muted/40 py-1 text-center font-mono text-[9px] text-muted-foreground">
                1
              </td>
              {HEADERS.map((header) => (
                <td
                  key={header}
                  className="border border-border/60 bg-card px-2 py-1 text-left font-semibold text-foreground/85"
                >
                  {header}
                </td>
              ))}
            </tr>
          </thead>

          <tbody>
            {DEMO_ROWS.map((row, i) => {
              const delta = deltaPct(row.q3, row.q4);
              return (
                <tr key={row.region}>
                  <td className="border border-border/60 bg-muted/40 py-1 text-center font-mono text-[9px] text-muted-foreground">
                    {i + 2}
                  </td>
                  <td className="truncate border border-border/60 px-2 py-1 text-foreground/80">
                    {row.region}
                  </td>
                  <td className="border border-border/60 px-2 py-1 text-right tabular-nums text-muted-foreground">
                    {row.q3}
                  </td>
                  <td className="border border-border/60 px-2 py-1 text-right tabular-nums text-foreground/80">
                    {row.q4}
                  </td>
                  <td
                    className={cn(
                      "border border-border/60 px-2 py-1 text-right font-medium tabular-nums transition-all duration-300",
                      computed
                        ? "demo-cell-hit text-[var(--accent-emerald)]"
                        : "text-transparent",
                    )}
                    style={{
                      transitionDelay: computed ? `${i * 110}ms` : "0ms",
                    }}
                  >
                    +{delta}%
                  </td>
                </tr>
              );
            })}

            <tr>
              <td className="border border-border/60 bg-muted/40 py-1 text-center font-mono text-[9px] text-muted-foreground">
                6
              </td>
              <td className="border border-border/60 px-2 py-1 font-semibold text-foreground/85">
                Total
              </td>
              <td className="border border-border/60 px-2 py-1 text-right tabular-nums text-muted-foreground">
                {DEMO_TOTALS.q3}
              </td>
              <td className="border border-border/60 px-2 py-1 text-right font-semibold tabular-nums text-foreground">
                {DEMO_TOTALS.q4}
              </td>
              <td
                className={cn(
                  "border border-border/60 px-2 py-1 text-right font-semibold tabular-nums transition-all duration-300",
                  computed
                    ? "demo-cell-hit text-[var(--accent-emerald)]"
                    : "text-transparent",
                )}
                style={{
                  transitionDelay: computed
                    ? `${DEMO_ROWS.length * 110}ms`
                    : "0ms",
                }}
              >
                +{deltaPct(DEMO_TOTALS.q3, DEMO_TOTALS.q4)}%
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
