import { cn } from "@/lib/utils";

/**
 * Shared 3D geometry for the homepage illustrations.
 *
 * Both primitives emit three flat polygons — top, left, right — and leave the
 * fills to `.dm-solid*` in `home.css`. Colour therefore never appears in a
 * component: an illustration re-themes entirely from the `--illus` token.
 *
 * Pure SVG output, so these render on the server and never hydrate.
 */

export type SolidTone = "strong" | "soft" | "deep" | "paper";

const TONE_CLASS: Record<SolidTone, string> = {
  strong: "",
  soft: "dm-solid--soft",
  deep: "dm-solid--deep",
  paper: "dm-solid--paper",
};

interface IsoBoxProps {
  /** Centre of the front-bottom edge. */
  cx: number;
  y: number;
  /** Half-width of the footprint; depth is derived as `a / 2` (2:1 isometric). */
  a: number;
  /** Height, upwards from `y`. */
  h: number;
  tone?: SolidTone;
}

/**
 * A true isometric cuboid, used for the bar chart.
 *
 * Footprint vertices at height 0 — front `(cx, y)`, right `(cx + a, y - b)`,
 * back `(cx, y - 2b)`, left `(cx - a, y - b)` — lifted by `h` for the top face.
 * Only three faces can ever be visible from this angle, so only three are drawn.
 */
export function IsoBox({ cx, y, a, h, tone = "strong" }: IsoBoxProps) {
  const b = a / 2;
  const t = y - h;

  return (
    <g className={cn("dm-solid", TONE_CLASS[tone])}>
      <polygon
        className="f-left"
        points={`${cx - a},${y - b} ${cx},${y} ${cx},${t} ${cx - a},${t - b}`}
      />
      <polygon
        className="f-right"
        points={`${cx},${y} ${cx + a},${y - b} ${cx + a},${t - b} ${cx},${t}`}
      />
      <polygon
        className="f-top"
        points={`${cx},${t} ${cx + a},${t - b} ${cx},${t - 2 * b} ${cx - a},${t - b}`}
      />
    </g>
  );
}

interface ObliqueBoxProps {
  /** Top-left corner of the front face. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Depth offset, drawn up and to the right. */
  d: number;
  tone?: SolidTone;
  className?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}

/**
 * A cabinet-projection box: the front face stays a true rectangle, so labels
 * and document lines sit on it undistorted. Used for the knowledge-base core
 * and its document satellites.
 */
export function ObliqueBox({
  x,
  y,
  w,
  h,
  d,
  tone = "strong",
  className,
  style,
  children,
}: ObliqueBoxProps) {
  return (
    <g className={cn("dm-solid", TONE_CLASS[tone], className)} style={style}>
      <polygon
        className="f-top"
        points={`${x},${y} ${x + d},${y - d} ${x + w + d},${y - d} ${x + w},${y}`}
      />
      <polygon
        className="f-right"
        points={`${x + w},${y} ${x + w + d},${y - d} ${x + w + d},${y + h - d} ${x + w},${y + h}`}
      />
      <polygon
        className="f-left"
        points={`${x},${y} ${x + w},${y} ${x + w},${y + h} ${x},${y + h}`}
      />
      {children}
    </g>
  );
}
