import {
  DEMO_ROWS,
  REGION_ABBR,
  deltaPct,
} from "@/components/home/agent-demo/demo-script";

/* Chart geometry, in viewBox units. */
const VIEW = { w: 340, h: 162 };
const PAD = { top: 14, right: 10, bottom: 26, left: 30 };
const PLOT = {
  w: VIEW.w - PAD.left - PAD.right,
  h: VIEW.h - PAD.top - PAD.bottom,
};
const Y_MAX = 500;
const BAR_W = 17;
const BAR_GAP = 5;
const GRID = [0, 250, 500];

const GROUP_W = PLOT.w / DEMO_ROWS.length;
const GROUP_INSET = (GROUP_W - (BAR_W * 2 + BAR_GAP)) / 2;

/** Data value → y pixel. */
const toY = (value: number) => PAD.top + PLOT.h - (value / Y_MAX) * PLOT.h;

interface DemoChartProps {
  /** Bars grow and the trend line draws once this flips true. */
  drawn: boolean;
  /** Reveal the per-region delta badges (a phase later than the bars). */
  showDeltas: boolean;
}

/**
 * The chart the agent "builds" — a grouped Q3/Q4 bar chart with a QoQ trend
 * line over the Q4 series.
 *
 * Animation is entirely declarative: bars carry a `--grown` scale and a
 * `--i` stagger index, the line retracts its own dash offset. No JavaScript
 * runs per frame.
 */
export function DemoChart({ drawn, showDeltas }: DemoChartProps) {
  const trend = DEMO_ROWS.map((row, i) => {
    const x = PAD.left + GROUP_W * i + GROUP_INSET + BAR_W + BAR_GAP / 2;
    return { x, y: toY(row.q4), row };
  });

  return (
    <svg
      viewBox={`0 0 ${VIEW.w} ${VIEW.h}`}
      className="h-full w-full"
      role="img"
      aria-label="Grouped bar chart comparing Q3 and Q4 revenue across four regions"
    >
      <defs>
        <linearGradient id="demo-q4-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent-cyan)" stopOpacity="0.95" />
          <stop
            offset="100%"
            stopColor="var(--accent-violet)"
            stopOpacity="0.55"
          />
        </linearGradient>
      </defs>

      {/* Gridlines + y axis */}
      {GRID.map((value) => (
        <g key={value}>
          <line
            x1={PAD.left}
            x2={VIEW.w - PAD.right}
            y1={toY(value)}
            y2={toY(value)}
            stroke="currentColor"
            strokeWidth="0.5"
            className="text-foreground/12"
          />
          <text
            x={PAD.left - 6}
            y={toY(value) + 3}
            textAnchor="end"
            className="fill-muted-foreground text-[7px]"
          >
            {value}
          </text>
        </g>
      ))}

      {DEMO_ROWS.map((row, i) => {
        const groupX = PAD.left + GROUP_W * i + GROUP_INSET;
        const bars = [
          { key: "q3", value: row.q3, x: groupX, fill: "var(--muted)" },
          {
            key: "q4",
            value: row.q4,
            x: groupX + BAR_W + BAR_GAP,
            fill: "url(#demo-q4-fill)",
          },
        ];

        return (
          <g key={row.region}>
            {bars.map((bar) => (
              <rect
                key={bar.key}
                x={bar.x}
                y={toY(bar.value)}
                width={BAR_W}
                height={PAD.top + PLOT.h - toY(bar.value)}
                rx="2.5"
                fill={bar.fill}
                className="demo-bar"
                style={
                  {
                    "--i": i * 2 + (bar.key === "q4" ? 1 : 0),
                    "--grown": drawn ? 1 : 0,
                  } as React.CSSProperties
                }
              />
            ))}

            <text
              x={groupX + BAR_W + BAR_GAP / 2}
              y={VIEW.h - 9}
              textAnchor="middle"
              className="fill-muted-foreground text-[7.5px]"
            >
              {REGION_ABBR[row.region] ?? row.region}
            </text>
          </g>
        );
      })}

      {/* QoQ trend over the Q4 series */}
      <polyline
        points={trend.map((p) => `${p.x},${p.y}`).join(" ")}
        fill="none"
        stroke="var(--accent-amber)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={100}
        data-drawn={drawn}
        className="demo-line"
        style={{ "--len": 100 } as React.CSSProperties}
      />

      {trend.map((p) => (
        <circle
          key={p.row.region}
          cx={p.x}
          cy={p.y}
          r="2"
          fill="var(--background)"
          stroke="var(--accent-amber)"
          strokeWidth="1.2"
          className="transition-opacity duration-500"
          style={{ opacity: drawn ? 1 : 0, transitionDelay: "1.1s" }}
        />
      ))}

      {/* Per-region delta badges, revealed with the insight */}
      {trend.map((p, i) => (
        <text
          key={`delta-${p.row.region}`}
          x={p.x}
          y={p.y - 7}
          textAnchor="middle"
          className="fill-[var(--accent-emerald)] text-[7px] font-semibold transition-opacity duration-500"
          style={{
            opacity: showDeltas ? 1 : 0,
            transitionDelay: `${i * 80}ms`,
          }}
        >
          +{deltaPct(p.row.q3, p.row.q4)}%
        </text>
      ))}
    </svg>
  );
}
