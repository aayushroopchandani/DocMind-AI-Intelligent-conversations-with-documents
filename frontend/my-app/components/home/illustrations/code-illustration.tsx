import { BarChart3, Braces, Sigma, type LucideIcon } from "lucide-react";

/**
 * The three languages a run is written in — Python, spreadsheet formulas and
 * the chart spec — stacked as light windows against the dark card.
 *
 * The stack fans apart on hover so all three headers stay readable; each pane
 * moves on `transform` only, so the hover costs nothing but a composite.
 */

interface CodeWindow {
  icon: LucideIcon;
  label: string;
  lines: readonly React.ReactNode[];
  /** Resting placement inside the stage. */
  style: React.CSSProperties;
  /** Travel on hover. */
  hx: string;
  hy: string;
}

const WINDOWS: readonly CodeWindow[] = [
  {
    icon: Braces,
    label: "Python",
    lines: [
      <>
        <span className="k">import</span> pandas <span className="k">as</span> pd
      </>,
      <>
        df = pd.read_csv(<span className="s">&quot;q4.csv&quot;</span>)
      </>,
      <>
        df.groupby(<span className="s">&quot;region&quot;</span>).sum()
      </>,
    ],
    style: { top: "0%", left: "0%", width: "76%" },
    hx: "-10px",
    hy: "-10px",
  },
  {
    icon: Sigma,
    label: "Formula",
    lines: [
      <>
        =<span className="k">ROUND</span>((C2-B2)/B2, <span className="n">3</span>)
      </>,
      <>
        =<span className="k">SUMIFS</span>(D:D, A:A, <span className="s">&quot;APAC&quot;</span>)
      </>,
      <>
        =<span className="k">XLOOKUP</span>(A2, Src!A:A, C:C)
      </>,
    ],
    style: { top: "31%", left: "16%", width: "80%" },
    hx: "11px",
    hy: "-2px",
  },
  {
    icon: BarChart3,
    label: "Chart",
    lines: [
      <>
        type: <span className="s">&quot;grouped-bar&quot;</span>
      </>,
      <>
        x: <span className="s">&quot;region&quot;</span>
      </>,
      <>
        y: [<span className="s">&quot;q3&quot;</span>, <span className="s">&quot;q4&quot;</span>]
      </>,
    ],
    style: { top: "62%", left: "5%", width: "76%" },
    hx: "-7px",
    hy: "10px",
  },
];

export function CodeIllustration() {
  return (
    <div className="dm-stack relative h-full w-full px-3">
      {WINDOWS.map(({ icon: Icon, label, lines, style, hx, hy }, i) => (
        <div
          key={label}
          className="dm-window dm-in absolute overflow-hidden"
          style={
            {
              ...style,
              "--i": i,
              "--hx": hx,
              "--hy": hy,
            } as React.CSSProperties
          }
        >
          <div className="dm-window-bar flex items-center gap-1.5 px-2 py-1">
            <Icon className="size-2.5" />
            <span className="text-[8px] font-semibold uppercase tracking-[0.14em]">
              {label}
            </span>
          </div>

          <div className="dm-code px-2 py-1.5 font-mono text-[8.5px] leading-[1.5]">
            {lines.map((line, n) => (
              <div key={n} className="flex gap-1.5 whitespace-nowrap">
                <span className="dm-gutter select-none">{n + 1}</span>
                <span className="truncate">{line}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
