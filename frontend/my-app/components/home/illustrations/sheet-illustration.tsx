import { Sigma } from "lucide-react";

/**
 * A worksheet with a column the agent just wrote.
 *
 * The point of the card is that output lands *in the artifact*, so the delta
 * column arrives cell by cell, already highlighted as a change, rather than
 * the whole table fading in at once.
 */

const HEADERS = ["Region", "Q3", "Q4", "Δ %"] as const;

const ROWS = [
  ["APAC", "226", "296", "+31.0%"],
  ["EMEA", "318", "351", "+10.4%"],
  ["AMER", "402", "428", "+6.5%"],
] as const;

export function SheetIllustration() {
  return (
    <div className="dm-sheet w-full font-mono text-[10px]">
      <div className="flex items-center gap-1.5 border-b border-border/70 px-2 py-1.5">
        <span className="rounded border border-border bg-background/60 px-1.5 py-0.5 text-[9px] text-muted-foreground">
          D2
        </span>
        <Sigma className="size-3 shrink-0 text-muted-foreground" />
        <span
          className="dm-in truncate text-[9.5px]"
          style={
            { color: "var(--illus)", "--i": 1 } as React.CSSProperties
          }
        >
          =ROUND((C2-B2)/B2*100, 1)
        </span>
      </div>

      <table className="w-full table-fixed border-collapse">
        <colgroup>
          <col className="w-[34%]" />
          <col className="w-[20%]" />
          <col className="w-[20%]" />
          <col className="w-[26%]" />
        </colgroup>

        <thead>
          <tr>
            {HEADERS.map((header) => (
              <th
                key={header}
                className="dm-sheet-cell bg-muted/40 px-2 py-1 text-left text-[9.5px] font-medium text-foreground/70"
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {ROWS.map(([region, q3, q4, delta], i) => (
            <tr key={region}>
              <td className="dm-sheet-cell truncate px-2 py-1 text-foreground/75">
                {region}
              </td>
              <td className="dm-sheet-cell px-2 py-1 text-right tabular-nums text-muted-foreground">
                {q3}
              </td>
              <td className="dm-sheet-cell px-2 py-1 text-right tabular-nums text-foreground/75">
                {q4}
              </td>
              <td
                className="dm-sheet-cell dm-sheet-cell--written dm-in px-2 py-1 text-right font-medium tabular-nums"
                style={
                  { "--i": i + 2, "--lead": "220ms" } as React.CSSProperties
                }
              >
                {delta}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
