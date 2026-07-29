import {
  ArrowUp,
  Braces,
  Database,
  FileSpreadsheet,
  FileText,
  Share2,
  Sparkles,
  Table2,
  type LucideIcon,
} from "lucide-react";

/**
 * Miniatures for the three-step journey panel.
 *
 * Each one is a shrunk-down piece of the real product surface — the input
 * tray, the composer, the result artifact — so the strip reads as a preview of
 * the workspace rather than generic marketing iconography.
 */

/* ------------------------------------------------------------------ */
/* 1 · Connect your data                                               */
/* ------------------------------------------------------------------ */

const INPUTS: readonly { icon: LucideIcon; label: string }[] = [
  { icon: FileSpreadsheet, label: "XLSX" },
  { icon: Table2, label: "CSV" },
  { icon: FileText, label: "PDF" },
  { icon: Braces, label: "JSON" },
  { icon: Database, label: "Tables" },
  { icon: FileText, label: "DOCX" },
];

export function ConnectIllustration() {
  return (
    <div className="grid w-full max-w-[210px] grid-cols-3 gap-2">
      {INPUTS.map(({ icon: Icon, label }, i) => (
        <span
          key={label}
          className="dm-source dm-in flex aspect-square flex-col items-center justify-center gap-1"
          style={{ "--i": i } as React.CSSProperties}
        >
          <Icon className="size-4" style={{ color: "var(--illus)" }} />
          <span className="font-mono text-[8px] tracking-wide text-muted-foreground">
            {label}
          </span>
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 2 · The analyst does the work                                       */
/* ------------------------------------------------------------------ */

const TRACE = [
  "Reading revenue.xlsx",
  "Grouping by region",
  "Writing the chart",
] as const;

export function AnalyzeIllustration() {
  return (
    <div className="flex w-full max-w-[228px] flex-col gap-2.5">
      <div
        className="dm-in flex items-center gap-2 rounded-xl border border-border bg-card/70 px-2.5 py-2"
        style={{ "--i": 0 } as React.CSSProperties}
      >
        <Sparkles
          className="size-3.5 shrink-0"
          style={{ color: "var(--illus)" }}
        />
        <span className="min-w-0 flex-1 truncate text-[11px] text-foreground/80">
          Analyse revenue by region
        </span>
        <span
          className="flex size-5 shrink-0 items-center justify-center rounded-lg"
          style={{ background: "var(--illus)" }}
        >
          <ArrowUp className="size-3 text-background" />
        </span>
      </div>

      {TRACE.map((step, i) => (
        <span
          key={step}
          className="dm-in flex items-center gap-2 pl-1 text-[10px] text-muted-foreground"
          style={{ "--i": i + 1, "--lead": "160ms" } as React.CSSProperties}
        >
          <span
            className="size-1 shrink-0 rounded-full"
            style={{ background: "var(--illus)" }}
          />
          {step}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 3 · Keep results you can reuse                                      */
/* ------------------------------------------------------------------ */

const RESULT: readonly [string, string, number][] = [
  ["North", "$2.1M", 100],
  ["South", "$1.6M", 76],
  ["East", "$1.2M", 57],
  ["West", "$0.9M", 43],
];

export function ShareIllustration() {
  return (
    <div className="dm-source w-full max-w-[228px] overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-2.5 py-2">
        <div className="flex -space-x-1.5">
          {[0.9, 0.6, 0.35].map((weight) => (
            <span
              key={weight}
              className="size-4 rounded-full border border-card"
              style={{
                background: `color-mix(in oklch, var(--illus) ${weight * 100}%, var(--background))`,
              }}
            />
          ))}
        </div>
        <span className="flex items-center gap-1 rounded-md border border-border bg-card px-1.5 py-0.5 text-[9px] text-muted-foreground">
          <Share2 className="size-2.5" />
          Share
        </span>
      </div>

      <div className="px-2.5 py-2">
        {RESULT.map(([region, value, width], i) => (
          <div
            key={region}
            className="dm-in flex items-center gap-2 py-[3px]"
            style={{ "--i": i, "--lead": "160ms" } as React.CSSProperties}
          >
            <span className="w-10 shrink-0 text-[10px] text-muted-foreground">
              {region}
            </span>
            <span className="w-11 shrink-0 text-right font-mono text-[10px] tabular-nums text-foreground/80">
              {value}
            </span>
            <span className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-foreground/5">
              <span
                className="block h-full rounded-full"
                style={{
                  width: `${width}%`,
                  background:
                    "linear-gradient(90deg, color-mix(in oklch, var(--illus) 45%, transparent), var(--illus))",
                }}
              />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
