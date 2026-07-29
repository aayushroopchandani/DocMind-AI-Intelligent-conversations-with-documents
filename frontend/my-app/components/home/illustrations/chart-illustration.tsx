import { IsoBox, type SolidTone } from "@/components/home/illustrations/solids";

/**
 * Isometric stacked bars that grow out of the ground plane when the card first
 * scrolls into view, then hold.
 *
 * The growth is a clip trick rather than a scale: the column group is clipped
 * at the baseline and starts translated below it, so each block keeps its true
 * proportions the whole way up — a `scaleY` would visibly squash the top face.
 */

const BASE_Y = 138;
const HALF_WIDTH = 27;
const DEPTH = HALF_WIDTH / 2;
const BLOCK_H = 21;

interface Column {
  cx: number;
  /** Tones bottom-to-top; the array length is the number of stacked blocks. */
  tones: readonly SolidTone[];
}

const COLUMNS: readonly Column[] = [
  { cx: 76, tones: ["deep", "strong"] },
  { cx: 140, tones: ["strong", "soft", "strong"] },
  { cx: 204, tones: ["deep", "strong", "soft", "strong"] },
];

export function ChartIllustration() {
  return (
    // Cropped to the drawing's real bounds rather than a round canvas size, so
    // the bars fill the card's stage instead of floating in their own margin.
    <svg viewBox="42 20 196 128" role="presentation" aria-hidden>
      <defs>
        {/* Everything below the baseline is hidden, which is what turns a
            downward offset into a "growing from the ground" entrance. */}
        <clipPath id="dm-chart-ground">
          <rect x="0" y="0" width="280" height={BASE_Y} />
        </clipPath>
      </defs>

      <g clipPath="url(#dm-chart-ground)">
        {COLUMNS.map(({ cx, tones }, col) => {
          const height = tones.length * BLOCK_H;

          return (
            <g
              key={cx}
              className="dm-rise"
              style={
                {
                  "--i": col,
                  // Clear the baseline by the full silhouette: stack height
                  // plus the depth of the top face.
                  "--rise": `${height + DEPTH * 2 + 6}px`,
                } as React.CSSProperties
              }
            >
              {tones.map((tone, row) => (
                <IsoBox
                  key={row}
                  cx={cx}
                  y={BASE_Y - row * BLOCK_H}
                  a={HALF_WIDTH}
                  h={BLOCK_H}
                  tone={tone}
                />
              ))}
            </g>
          );
        })}
      </g>

      {/* Ground plane, drawn outside the clip so it stays put while the bars
          travel up through it. */}
      <line
        className="dm-ground"
        x1={COLUMNS[0].cx - HALF_WIDTH - 6}
        y1={BASE_Y + 4}
        x2={COLUMNS[COLUMNS.length - 1].cx + HALF_WIDTH + 6}
        y2={BASE_Y + 4}
      />
    </svg>
  );
}
