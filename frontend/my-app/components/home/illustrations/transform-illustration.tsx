/**
 * Messy source values settling into a clean, typed table.
 *
 * HTML rather than SVG: this illustration is really just text on cards, and
 * real text stays crisp at any zoom and reflows with the card. The scattered
 * chips animate from an unrotated offset into their resting rotation, so the
 * "settling" reads as intentional rather than a wobble.
 */

interface MessyValue {
  month: string;
  raw: string;
  /** Resting position and rotation. */
  top: string;
  left: string;
  rot: number;
  /** Travel vector the chip arrives from. */
  fx: number;
  fy: number;
}

const MESSY: readonly MessyValue[] = [
  { month: "Feb", raw: "$42k", top: "2%", left: "4%", rot: -7, fx: -16, fy: 14 },
  { month: "Mar", raw: "85000", top: "27%", left: "20%", rot: 5, fx: -12, fy: 18 },
  { month: "Jan", raw: "$120k", top: "53%", left: "1%", rot: -4, fx: -18, fy: -10 },
  { month: "Apr", raw: "67,000", top: "78%", left: "16%", rot: 7, fx: -14, fy: 16 },
];

const CLEAN: readonly [string, string][] = [
  ["Jan", "$120k"],
  ["Feb", "$42k"],
  ["Mar", "$85k"],
  ["Apr", "$67k"],
];

export function TransformIllustration() {
  return (
    <div className="flex h-full w-full items-center gap-2 px-4">
      {/* Unstructured input */}
      <div className="relative h-[150px] flex-1">
        {MESSY.map((item, i) => (
          <span
            key={item.month}
            className="dm-chip-in dm-tag absolute flex items-center gap-1 px-1.5 py-1 font-mono text-[10px] font-medium"
            style={
              {
                top: item.top,
                left: item.left,
                "--i": i,
                "--rot": `${item.rot}deg`,
                "--fx": `${item.fx}px`,
                "--fy": `${item.fy}px`,
              } as React.CSSProperties
            }
          >
            <span className="dm-tag-key">{item.month}</span>
            {item.raw}
          </span>
        ))}
      </div>

      <svg
        viewBox="0 0 22 20"
        className="dm-in size-5 shrink-0"
        style={{ "--i": 4 } as React.CSSProperties}
        role="presentation"
        aria-hidden
      >
        <path
          d="M1 7h9V2l11 8-11 8v-5H1z"
          fill="var(--illus)"
          stroke="color-mix(in oklch, black 40%, transparent)"
          strokeWidth="1"
          strokeLinejoin="round"
        />
      </svg>

      {/* Analysis-ready output */}
      <div
        className="dm-table dm-in w-[108px] shrink-0 overflow-hidden font-mono text-[10px]"
        style={{ "--i": 5 } as React.CSSProperties}
      >
        <div className="dm-table-head flex justify-between px-2 py-1 font-semibold">
          <span>Month</span>
          <span>Amount</span>
        </div>
        {CLEAN.map(([month, amount], i) => (
          <div
            key={month}
            className={`dm-table-row dm-in flex justify-between px-2 py-1 ${
              i % 2 === 1 ? "dm-table-row--hit" : ""
            }`}
            style={{ "--i": 6 + i } as React.CSSProperties}
          >
            <span>{month}</span>
            <span className="font-medium tabular-nums">{amount}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
