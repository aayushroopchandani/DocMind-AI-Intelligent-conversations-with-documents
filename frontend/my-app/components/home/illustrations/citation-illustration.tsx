import { FileSpreadsheet, FileText } from "lucide-react";

/**
 * A page with the cited line highlighted, wired to the sources it produced.
 *
 * The highlight lands first and the source chips follow, which is the order
 * the product works in: the evidence exists before the citation does.
 */

/** Widths of the body lines; index 3 is the one the answer came from. */
const LINES = ["78%", "94%", "62%", "88%", "70%", "96%", "54%"];
const CITED = 3;

/** Trace branches, drawn in a 28×52 box from the page edge out to each chip. */
const BRANCHES = [
  "M0 26h8a5 5 0 0 0 5-5v-6a5 5 0 0 1 5-5h10",
  "M0 26h8a5 5 0 0 1 5 5v6a5 5 0 0 0 5 5h10",
] as const;

const SOURCES = [
  { icon: FileText, label: "Q4-report.pdf p.42" },
  { icon: FileSpreadsheet, label: "revenue.xlsx D2:D6" },
] as const;

export function CitationIllustration() {
  return (
    <div className="flex w-full items-center gap-2">
      {/* Source page */}
      <div className="dm-source w-[68px] shrink-0 space-y-[5px] p-2">
        <div className="mb-2 h-1.5 w-1/2 rounded-full bg-foreground/25" />
        {LINES.map((width, i) => (
          <div
            key={i}
            className={`dm-in h-[5px] rounded-full ${
              i === CITED ? "dm-highlight" : "bg-foreground/10"
            }`}
            style={
              {
                width,
                "--i": i === CITED ? 0 : i + 1,
              } as React.CSSProperties
            }
          />
        ))}
      </div>

      {/* Trace from the highlighted line out to each citation. */}
      <svg
        viewBox="0 0 28 52"
        className="h-12 w-7 shrink-0"
        role="presentation"
        aria-hidden
      >
        {BRANCHES.map((d, i) => (
          <path
            key={d}
            className="dm-draw"
            pathLength="1"
            d={d}
            fill="none"
            stroke="var(--illus)"
            strokeWidth="1.5"
            strokeLinecap="round"
            style={{ "--i": i } as React.CSSProperties}
          />
        ))}
      </svg>

      <div className="flex min-w-0 flex-1 flex-col gap-2">
        {SOURCES.map(({ icon: Icon, label }, i) => (
          <span
            key={label}
            className="dm-source dm-in flex items-center gap-1.5 truncate px-2 py-1.5 font-mono text-[9.5px] text-muted-foreground"
            style={
              { "--i": i, "--lead": "620ms" } as React.CSSProperties
            }
          >
            <Icon className="size-3 shrink-0" style={{ color: "var(--illus)" }} />
            <span className="truncate">{label}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
