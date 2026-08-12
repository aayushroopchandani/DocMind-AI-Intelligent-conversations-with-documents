import type { IStyleData } from "@univerjs/core";
import {
  getActiveRange,
  getApi,
  withRange,
} from "@/lib/data-analysis/sheet/sheet-api";

/**
 * Format menu behaviour: everything here runs entirely in the browser
 * through the Univer facade, so no backend is involved.
 *
 * Toggles read the *anchor* cell's resolved style (the first cell of the
 * selection) and then apply the opposite value across the whole selection —
 * the same rule Excel and Sheets use, so a mixed selection normalises on the
 * first click instead of flipping cell by cell.
 *
 * Univer enums are read off the live facade (`api.Enum`) rather than
 * imported from `@univerjs/core`: this module is reachable from the always-
 * mounted menu bar, and a value import would drag the whole engine into the
 * route's first-load bundle instead of the lazy Univer chunk.
 */

const BOOLEAN_ON = 1;

function anchorStyle(): IStyleData | null {
  try {
    return getActiveRange()?.getCellStyleData() ?? null;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* Text style                                                          */
/* ------------------------------------------------------------------ */

export function isBold(): boolean {
  return anchorStyle()?.bl === BOOLEAN_ON;
}

export function isItalic(): boolean {
  return anchorStyle()?.it === BOOLEAN_ON;
}

export function isUnderlined(): boolean {
  return anchorStyle()?.ul?.s === BOOLEAN_ON;
}

export function isStruckThrough(): boolean {
  return anchorStyle()?.st?.s === BOOLEAN_ON;
}

export function toggleBold(): void {
  const next = isBold() ? "normal" : "bold";
  withRange((range) => range.setFontWeight(next));
}

export function toggleItalic(): void {
  const next = isItalic() ? "normal" : "italic";
  withRange((range) => range.setFontStyle(next));
}

export function toggleUnderline(): void {
  const next = isUnderlined() ? "none" : "underline";
  withRange((range) => range.setFontLine(next));
}

export function toggleStrikethrough(): void {
  const next = isStruckThrough() ? "none" : "line-through";
  withRange((range) => range.setFontLine(next));
}

export function setFontSize(size: number): void {
  withRange((range) => range.setFontSize(size));
}

export function setFontFamily(family: string): void {
  withRange((range) => range.setFontFamily(family));
}

export function setFontColor(color: string | null): void {
  withRange((range) => range.setFontColor(color));
}

export function setBackgroundColor(color: string): void {
  withRange((range) => range.setBackgroundColor(color));
}

/* ------------------------------------------------------------------ */
/* Alignment and wrapping                                              */
/* ------------------------------------------------------------------ */

export type HorizontalAlignment = "left" | "center" | "normal";
export type VerticalAlignment = "top" | "middle" | "bottom";

export function setHorizontalAlignment(alignment: HorizontalAlignment): void {
  withRange((range) => range.setHorizontalAlignment(alignment));
}

export function setVerticalAlignment(alignment: VerticalAlignment): void {
  withRange((range) => range.setVerticalAlignment(alignment));
}

export function isWrapped(): boolean {
  try {
    return getActiveRange()?.getWrap() ?? false;
  } catch {
    return false;
  }
}

export function toggleWrap(): void {
  const next = !isWrapped();
  withRange((range) => range.setWrap(next));
}

/* ------------------------------------------------------------------ */
/* Merging                                                             */
/* ------------------------------------------------------------------ */

export function isMerged(): boolean {
  try {
    return getActiveRange()?.isPartOfMerge() ?? false;
  } catch {
    return false;
  }
}

export function mergeSelection(): void {
  withRange((range) => range.merge());
}

export function mergeAcross(): void {
  withRange((range) => range.mergeAcross());
}

export function unmergeSelection(): void {
  withRange((range) => range.breakApart());
}

/* ------------------------------------------------------------------ */
/* Number formats                                                      */
/* ------------------------------------------------------------------ */

/** Excel-compatible patterns, matching what the Univer numfmt plugin reads. */
export const NUMBER_FORMATS = {
  automatic: "General",
  plainText: "@",
  number: "#,##0.00",
  integer: "#,##0",
  percent: "0.00%",
  currency: '"$"#,##0.00',
  accounting: '_("$"* #,##0.00_)',
  scientific: "0.00E+00",
  date: "yyyy-mm-dd",
  time: "hh:mm:ss",
  dateTime: "yyyy-mm-dd hh:mm",
  duration: "[h]:mm:ss",
} as const;

export type NumberFormatKey = keyof typeof NUMBER_FORMATS;

export function setNumberFormat(key: NumberFormatKey): void {
  withRange((range) => range.setNumberFormat(NUMBER_FORMATS[key]));
}

/* ------------------------------------------------------------------ */
/* Borders                                                             */
/* ------------------------------------------------------------------ */

export type BorderPreset =
  | "all"
  | "outside"
  | "inside"
  | "top"
  | "bottom"
  | "left"
  | "right"
  | "none";

const BORDER_TYPE_KEYS = {
  all: "ALL",
  outside: "OUTSIDE",
  inside: "INSIDE",
  top: "TOP",
  bottom: "BOTTOM",
  left: "LEFT",
  right: "RIGHT",
  none: "NONE",
} as const satisfies Record<BorderPreset, string>;

export function setBorder(preset: BorderPreset, color = "#8c8c8c"): void {
  const enums = getApi()?.Enum;
  if (!enums) return;
  const type = enums.BorderType[BORDER_TYPE_KEYS[preset]];
  const style =
    preset === "none"
      ? enums.BorderStyleTypes.NONE
      : enums.BorderStyleTypes.THIN;
  withRange((range) => range.setBorder(type, style, color));
}

/* ------------------------------------------------------------------ */
/* Clearing                                                            */
/* ------------------------------------------------------------------ */

export function clearFormatting(): void {
  withRange((range) => range.clearFormat());
}
