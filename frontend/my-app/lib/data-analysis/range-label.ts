import type { IRange } from "@univerjs/core";

/** Converts a zero-based column index to its A1 letter (0 → A, 26 → AA). */
export function columnLabel(index: number): string {
  let label = "";
  let current = index;
  while (current >= 0) {
    label = String.fromCharCode((current % 26) + 65) + label;
    current = Math.floor(current / 26) - 1;
  }
  return label;
}

/** Formats a Univer range as compact A1 notation, e.g. "A1" or "A1:D20". */
export function formatRangeA1(range: IRange): string {
  const start = `${columnLabel(range.startColumn)}${range.startRow + 1}`;
  const end = `${columnLabel(range.endColumn)}${range.endRow + 1}`;
  return start === end ? start : `${start}:${end}`;
}
