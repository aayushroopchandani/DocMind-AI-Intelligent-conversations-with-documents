import { Quote } from "lucide-react";
import type { Capability } from "@/components/home/data/homepage-content";

/**
 * Decorative miniatures for the wider bento tiles.
 *
 * Static markup only — the tiles already animate on hover through the shared
 * `.bento-card` wash, so these stay cheap and never re-render.
 */

function SheetVisual() {
  const rows = [
    ["Region", "Q3", "Q4", "Δ %"],
    ["APAC", "226", "296", "+31.0%"],
    ["EMEA", "318", "351", "+10.4%"],
  ];

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background/50">
      <div className="border-b border-border/70 px-2.5 py-1.5 font-mono text-[9.5px] text-[var(--card-accent)]">
        =ROUND((C2-B2)/B2*100, 1)
      </div>
      <table className="w-full table-fixed border-collapse text-[10px]">
        <tbody>
          {rows.map((row, r) => (
            <tr key={row[0]}>
              {row.map((cell, c) => (
                <td
                  key={cell}
                  className={[
                    "border border-border/50 px-2 py-1 truncate",
                    r === 0 ? "font-medium text-foreground/70" : "",
                    c > 0 ? "text-right tabular-nums" : "",
                    r > 0 && c === 3
                      ? "accent-text font-medium"
                      : r > 0
                        ? "text-muted-foreground"
                        : "",
                  ].join(" ")}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChartVisual() {
  const bars = [38, 62, 47, 84, 58, 96];

  return (
    <div className="flex h-24 items-end gap-1.5 rounded-lg border border-border bg-background/50 p-3">
      {bars.map((height, i) => (
        <div
          key={i}
          className="flex-1 rounded-t-[3px] transition-all duration-500 group-hover:opacity-100"
          style={{
            height: `${height}%`,
            background:
              "linear-gradient(to top, color-mix(in oklch, var(--card-accent) 30%, transparent), var(--card-accent))",
            opacity: 0.45 + i * 0.09,
          }}
        />
      ))}
    </div>
  );
}

function CitationVisual() {
  return (
    <div className="flex items-stretch gap-3">
      <div className="w-24 shrink-0 space-y-1.5 rounded-lg border border-border bg-background/50 p-2.5">
        <div className="h-1.5 w-2/3 rounded bg-foreground/20" />
        <div className="h-1.5 w-full rounded bg-foreground/10" />
        <div
          className="h-1.5 w-11/12 rounded"
          style={{
            background:
              "color-mix(in oklch, var(--card-accent) 55%, transparent)",
          }}
        />
        <div className="h-1.5 w-4/5 rounded bg-foreground/10" />
        <div className="h-1.5 w-full rounded bg-foreground/10" />
      </div>

      <div className="flex min-w-0 flex-1 flex-col justify-center gap-1.5">
        {["Q4-report.pdf · p.42", "revenue.xlsx · Sheet1!D2:D6"].map((source) => (
          <span
            key={source}
            className="flex items-center gap-1.5 truncate rounded-lg border border-border bg-card px-2 py-1.5 font-mono text-[9.5px] text-muted-foreground"
          >
            <Quote className="size-2.5 shrink-0 accent-text" />
            {source}
          </span>
        ))}
      </div>
    </div>
  );
}

const VISUALS = {
  sheet: SheetVisual,
  chart: ChartVisual,
  citation: CitationVisual,
} satisfies Record<NonNullable<Capability["visual"]>, () => React.ReactElement>;

export function CapabilityVisual({
  variant,
}: {
  variant: NonNullable<Capability["visual"]>;
}) {
  const Visual = VISUALS[variant];
  return <Visual />;
}
