/**
 * Number and date formatting for analysis surfaces.
 *
 * Everything here pins an explicit locale, and that is the whole point.
 * `toLocaleString()` and `new Intl.NumberFormat(undefined, …)` resolve against
 * the *runtime's* locale, which is not the same on the server as in the
 * browser — a result rendered during SSR under `en-US` and rehydrated under
 * `en-GB` produces different text for the same number, and React fails
 * hydration. The analyst UI is written in one language, so it formats in one
 * locale too, and server and client agree by construction.
 *
 * It also makes the formatting testable: a check that expects `98,211.50` is
 * asserting behaviour rather than the locale of whichever machine ran it.
 *
 * Formatters are constructed once and reused. Building an `Intl.NumberFormat`
 * is comparatively expensive, and a result table asks for the same one several
 * hundred times.
 */

export const UI_LOCALE = "en-US";

/** Whole numbers with thousands separators: counts, rows, bytes. */
const COUNT = new Intl.NumberFormat(UI_LOCALE, { maximumFractionDigits: 0 });

/** Up to six decimals, for decimals and percentages. */
const DECIMAL = new Intl.NumberFormat(UI_LOCALE, { maximumFractionDigits: 6 });

/** Exactly two decimals, for money. */
const MONEY = new Intl.NumberFormat(UI_LOCALE, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const DATE = new Intl.DateTimeFormat(UI_LOCALE, {
  year: "numeric",
  month: "short",
  day: "numeric",
});

const DATE_TIME = new Intl.DateTimeFormat(UI_LOCALE, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export type NumberStyle = "count" | "decimal" | "money";

export function numberFormatter(style: NumberStyle): Intl.NumberFormat {
  if (style === "count") return COUNT;
  if (style === "money") return MONEY;
  return DECIMAL;
}

/** A count with separators — `4200` becomes `4,200`. */
export function formatCount(value: number): string {
  return COUNT.format(value);
}

/**
 * Reformat an ISO instant for reading.
 *
 * The publisher serialises dates as ISO strings, so this reformats rather than
 * parses arbitrary text. Anything unparseable is returned untouched: showing
 * the raw value beats inventing one.
 */
export function formatInstant(value: string, withTime: boolean): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return withTime ? DATE_TIME.format(parsed) : DATE.format(parsed);
}
