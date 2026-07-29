import { ObliqueBox } from "@/components/home/illustrations/solids";

/**
 * The context picker: loose documents orbiting the run they feed.
 *
 * Hovering the card pulls every satellite into the core and fades it out —
 * the selection becoming the agent's context, in one gesture. Each satellite
 * carries its own vector to the core as `--mx`/`--my`, so the merge is four
 * independent `transform` transitions and nothing recalculates on the main
 * thread.
 */

const CORE = { x: 105, y: 78, w: 70, h: 76, d: 16 } as const;
const CORE_CENTER = {
  x: CORE.x + CORE.w / 2 + CORE.d / 2,
  y: CORE.y + CORE.h / 2 - CORE.d / 2,
};

const SAT = { w: 32, h: 36, d: 7 } as const;

const SATELLITES = [
  { label: "PDF", x: 40, y: 50 },
  { label: "DOCX", x: 34, y: 112 },
  { label: "XLSX", x: 206, y: 58 },
  { label: "CSV", x: 214, y: 118 },
] as const;

export function ContextIllustration() {
  return (
    // Cropped to the composition's bounds so the core reads at a legible size
    // in a card-sized stage.
    <svg viewBox="26 36 232 158" role="presentation" aria-hidden>
      {/* Crosshair guides — they fade out as the sources merge. */}
      <line
        className="dm-guide"
        x1="24"
        y1={CORE_CENTER.y}
        x2="256"
        y2={CORE_CENTER.y}
      />
      <line
        className="dm-guide"
        x1={CORE_CENTER.x}
        y1="26"
        x2={CORE_CENTER.x}
        y2="176"
      />

      {/* Contact shadow, so the core reads as sitting on a surface. */}
      <ellipse
        cx={CORE_CENTER.x}
        cy={CORE.y + CORE.h + 14}
        rx="54"
        ry="9"
        fill="color-mix(in oklch, black 55%, transparent)"
      />

      <g className="dm-core">
        <ObliqueBox {...CORE} tone="strong">
          {[0, 1, 2].map((i) => (
            <line
              key={i}
              x1={CORE.x + 12}
              y1={CORE.y + 14 + i * 8}
              x2={CORE.x + CORE.w - (i === 2 ? 26 : 12)}
              y2={CORE.y + 14 + i * 8}
              stroke="color-mix(in oklch, black 42%, transparent)"
              strokeWidth="1.75"
              strokeLinecap="round"
            />
          ))}
          <circle
            cx={CORE.x + CORE.w / 2}
            cy={CORE.y + CORE.h / 2 + 8}
            r="9"
            fill="none"
            stroke="color-mix(in oklch, black 42%, transparent)"
            strokeWidth="1.75"
          />
        </ObliqueBox>
      </g>

      {SATELLITES.map(({ label, x, y }, i) => {
        const center = { x: x + SAT.w / 2 + SAT.d / 2, y: y + SAT.h / 2 - SAT.d / 2 };

        return (
          <g
            key={label}
            className="dm-in"
            style={{ "--i": i + 1, "--lead": "160ms" } as React.CSSProperties}
          >
            <g
              className="dm-sat"
              style={
                {
                  "--mx": `${CORE_CENTER.x - center.x}px`,
                  "--my": `${CORE_CENTER.y - center.y}px`,
                } as React.CSSProperties
              }
            >
              <ObliqueBox x={x} y={y} {...SAT} tone="paper">
                {[0, 1].map((n) => (
                  <line
                    key={n}
                    x1={x + 6}
                    y1={y + 9 + n * 5}
                    x2={x + SAT.w - (n === 1 ? 12 : 6)}
                    y2={y + 9 + n * 5}
                    stroke="var(--ink-soft)"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                  />
                ))}
                <text
                  x={x + SAT.w / 2}
                  y={y + SAT.h - 9}
                  textAnchor="middle"
                  fill="var(--ink)"
                  fontSize="8.5"
                  fontWeight="600"
                  fontFamily="var(--font-mono)"
                  letterSpacing="0.04em"
                >
                  {label}
                </text>
              </ObliqueBox>
            </g>
          </g>
        );
      })}

      <text
        className="dm-context-hint"
        x={CORE_CENTER.x}
        y="188"
        textAnchor="middle"
        fill="var(--illus)"
        fontSize="9.5"
        fontWeight="500"
      >
        4 sources in context
      </text>
    </svg>
  );
}
