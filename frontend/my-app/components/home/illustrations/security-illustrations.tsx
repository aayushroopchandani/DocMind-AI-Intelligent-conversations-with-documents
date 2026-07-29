import { Check, Lock, X } from "lucide-react";
import { ObliqueBox } from "@/components/home/illustrations/solids";

/**
 * The three trust illustrations.
 *
 * Kept in one module because they share a visual language — outline strokes in
 * `--illus-line`, solids in `--illus`, paper hardware — and are never used
 * apart from each other.
 */

/* ------------------------------------------------------------------ */
/* Ownership — your workspace, sealed                                  */
/* ------------------------------------------------------------------ */

const CLOUD =
  "M46 106C22 106 16 76 38 68C30 42 60 26 78 42C88 18 128 16 138 40C164 30 190 50 182 74C202 82 198 106 174 106Z";

export function OwnershipIllustration() {
  return (
    <svg viewBox="0 0 220 130" role="presentation" aria-hidden>
      <path
        className="dm-in"
        d={CLOUD}
        fill="color-mix(in oklch, var(--foreground) 3%, transparent)"
        stroke="var(--illus-line)"
        strokeWidth="2"
        strokeLinejoin="round"
        style={{ "--i": 0 } as React.CSSProperties}
      />

      <g
        className="dm-in"
        style={{ "--i": 1, "--lead": "180ms" } as React.CSSProperties}
      >
        <ObliqueBox x={88} y={54} w={44} h={40} d={11} tone="strong">
          <circle
            cx={110}
            cy={70}
            r="5.5"
            fill="none"
            stroke="color-mix(in oklch, black 45%, transparent)"
            strokeWidth="2.5"
          />
          <path
            d="M110 74.5v8"
            stroke="color-mix(in oklch, black 45%, transparent)"
            strokeWidth="2.5"
            strokeLinecap="round"
          />
        </ObliqueBox>
      </g>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Encryption — protected in transit and at rest                       */
/* ------------------------------------------------------------------ */

/** A paper server: two drive slots and a status light. */
function ServerBox({ x, y }: { x: number; y: number }) {
  return (
    <ObliqueBox x={x} y={y} w={42} h={28} d={10} tone="paper">
      {[0, 1].map((i) => (
        <line
          key={i}
          x1={x + 7}
          y1={y + 10 + i * 9}
          x2={x + 25}
          y2={y + 10 + i * 9}
          stroke="var(--ink-soft)"
          strokeWidth="2"
          strokeLinecap="round"
        />
      ))}
      <circle cx={x + 34} cy={y + 10} r="2.5" fill="var(--illus)" />
    </ObliqueBox>
  );
}

/** A padlock node sitting on the transit path. */
function LockNode({ cx, cy }: { cx: number; cy: number }) {
  return (
    <>
      <circle
        cx={cx}
        cy={cy}
        r="6.5"
        fill="var(--background)"
        stroke="var(--illus)"
        strokeWidth="1.75"
      />
      <path
        d={`M${cx - 1.9} ${cy - 0.8}v-1.7a1.9 1.9 0 0 1 3.8 0v1.7`}
        fill="none"
        stroke="var(--illus)"
        strokeWidth="1.1"
      />
      <rect
        x={cx - 3}
        y={cy - 0.8}
        width="6"
        height="4.6"
        rx="1"
        fill="var(--illus)"
      />
    </>
  );
}

const TRANSIT = "M60 58C84 58 86 75 110 75C134 75 136 92 160 92";

export function EncryptionIllustration() {
  return (
    <svg viewBox="0 0 220 130" role="presentation" aria-hidden>
      <g className="dm-in" style={{ "--i": 0 } as React.CSSProperties}>
        <ServerBox x={18} y={34} />
      </g>

      <path
        className="dm-dash"
        d={TRANSIT}
        fill="none"
        stroke="var(--illus)"
        strokeWidth="2"
        strokeLinecap="round"
        style={{ "--i": 0 } as React.CSSProperties}
      />

      <g
        className="dm-in"
        style={{ "--i": 1, "--lead": "300ms" } as React.CSSProperties}
      >
        <ServerBox x={158} y={78} />
      </g>

      <g
        className="dm-in"
        style={{ "--i": 2, "--lead": "460ms" } as React.CSSProperties}
      >
        <LockNode cx={80} cy={60.5} />
      </g>
      <g
        className="dm-in"
        style={{ "--i": 3, "--lead": "460ms" } as React.CSSProperties}
      >
        <LockNode cx={140} cy={89.5} />
      </g>

      {/* Shield seal at the midpoint of the path. */}
      <g
        className="dm-in"
        style={{ "--i": 4, "--lead": "560ms" } as React.CSSProperties}
      >
        <path
          d="M110 59l13 4.5v11c0 8.5-5.5 14-13 16.5-7.5-2.5-13-8-13-16.5v-11z"
          fill="color-mix(in oklch, var(--illus) 22%, var(--background))"
          stroke="var(--illus)"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path
          d="M104 73.5l4 4 8-8.5"
          fill="none"
          stroke="var(--illus)"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Sandbox — what a single run can reach                               */
/* ------------------------------------------------------------------ */

const IN_SCOPE = ["Q4-report.pdf", "revenue.xlsx"] as const;
const OUT_OF_SCOPE = ["outbound network", "other workspaces"] as const;

export function SandboxIllustration() {
  return (
    <div className="w-full rounded-xl border border-border bg-background/40 p-3.5">
      <div className="mb-3 flex items-center gap-1.5">
        <Lock className="size-3" style={{ color: "var(--illus)" }} />
        <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          Run sandbox
        </span>
      </div>

      <p className="mb-1.5 text-[10px] text-muted-foreground/80">In scope</p>
      <div className="mb-3.5 flex flex-wrap gap-1.5">
        {IN_SCOPE.map((item, i) => (
          <span
            key={item}
            className="dm-in flex items-center gap-1 rounded-md px-1.5 py-1 font-mono text-[9.5px]"
            style={
              {
                "--i": i,
                color: "var(--illus)",
                background: "color-mix(in oklch, var(--illus) 12%, transparent)",
                border:
                  "1px solid color-mix(in oklch, var(--illus) 28%, transparent)",
              } as React.CSSProperties
            }
          >
            <Check className="size-2.5 shrink-0" />
            {item}
          </span>
        ))}
      </div>

      <p className="mb-1.5 text-[10px] text-muted-foreground/80">Unreachable</p>
      <div className="flex flex-wrap gap-1.5">
        {OUT_OF_SCOPE.map((item, i) => (
          <span
            key={item}
            className="dm-in flex items-center gap-1 rounded-md border border-border bg-card/50 px-1.5 py-1 font-mono text-[9.5px] text-muted-foreground/70"
            style={{ "--i": i + 2 } as React.CSSProperties}
          >
            <X className="size-2.5 shrink-0" />
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}
