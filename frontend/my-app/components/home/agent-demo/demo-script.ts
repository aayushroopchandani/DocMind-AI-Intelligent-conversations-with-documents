/**
 * The scripted run the homepage console plays on a loop.
 *
 * Everything the demo shows lives here as plain data so the components stay
 * presentational: change a number, a step label or a hold time and the whole
 * sequence follows.
 */

/** Ordered phases of the run. Indexes are compared with `>=` in components. */
export const Phase = {
  Idle: 0,
  Typing: 1,
  Thinking: 2,
  Sheet: 3,
  Chart: 4,
  Insight: 5,
  Hold: 6,
} as const;

/** Hold time in ms for each phase, indexed by `Phase`. */
export const PHASE_DURATIONS = [
  700, // Idle      — a beat before the cursor starts
  2300, // Typing    — covers the query at ~26ms/char
  2100, // Thinking  — four streamed steps
  1900, // Sheet     — computed column fills in
  1900, // Chart     — bars grow, trend line draws
  3400, // Insight   — long enough to actually read
  900, // Hold      — settle, then loop
] as const;

export const DEMO_QUERY =
  "Compare Q3 vs Q4 revenue by region and chart it";

export interface DemoStep {
  label: string;
  detail: string;
}

/** Agent trace lines, revealed one per ~90ms stagger during `Thinking`. */
export const DEMO_STEPS: readonly DemoStep[] = [
  { label: "Reading", detail: "revenue.xlsx · Sheet1!A1:C5" },
  { label: "Cleaning", detail: "3 nulls coerced, 1 duplicate dropped" },
  { label: "Running", detail: "pandas · groupby(region).sum()" },
  { label: "Rendering", detail: "grouped bar + QoQ trend" },
] as const;

export interface RegionRow {
  region: string;
  q3: number;
  q4: number;
}

/** Revenue in thousands. Deltas are derived, never hard-coded. */
export const DEMO_ROWS: readonly RegionRow[] = [
  { region: "North America", q3: 412, q4: 468 },
  { region: "EMEA", q3: 318, q4: 351 },
  { region: "APAC", q3: 226, q4: 296 },
  { region: "LATAM", q3: 141, q4: 168 },
] as const;

/** Short axis labels — the full region names are too wide under the bars. */
export const REGION_ABBR: Record<string, string> = {
  "North America": "NA",
  EMEA: "EMEA",
  APAC: "APAC",
  LATAM: "LATAM",
};

export const DEMO_FORMULA = "=ROUND((C2-B2)/B2*100, 1)";

/**
 * Percentage change between two quarters, always to one decimal place.
 *
 * Returns a string rather than a number so a whole result reads "+31.0%"
 * alongside "+13.6%" instead of collapsing to "+31%".
 */
export function deltaPct(from: number, to: number): string {
  return (((to - from) / from) * 100).toFixed(1);
}

export const DEMO_TOTALS = DEMO_ROWS.reduce(
  (acc, row) => ({ q3: acc.q3 + row.q3, q4: acc.q4 + row.q4 }),
  { q3: 0, q4: 0 },
);

/** The region with the largest QoQ gain — drives the insight headline. */
export const DEMO_TOP_MOVER = DEMO_ROWS.reduce((best, row) =>
  // Compare the raw ratios; `deltaPct` returns a display string.
  (row.q4 - row.q3) / row.q3 > (best.q4 - best.q3) / best.q3 ? row : best,
);
