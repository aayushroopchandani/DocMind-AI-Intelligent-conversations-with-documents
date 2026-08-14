/**
 * The workbook interchange model, mirroring the backend's Pydantic schema in
 * `scripts/data_analysis_agent/spreadsheet_io/workbook_model.py`.
 *
 * This is what crosses the wire for spreadsheet import and export. It is
 * engine-neutral on purpose: the backend never learns Univer's internals, and
 * the small mapping into `IWorkbookData` lives on this side, next to Univer.
 */

export const WORKBOOK_DOCUMENT_SCHEMA_VERSION = 1;

export type DocumentCellType =
  | "blank"
  | "string"
  | "number"
  | "boolean"
  | "date"
  | "formula"
  | "error";

export type DocumentHorizontalAlignment =
  | "general"
  | "left"
  | "center"
  | "right"
  | "justify";

export type DocumentVerticalAlignment = "top" | "middle" | "bottom";

export type DocumentBorderStyle =
  | "none"
  | "thin"
  | "medium"
  | "thick"
  | "dashed"
  | "dotted"
  | "double";

export interface DocumentBorderEdge {
  style: DocumentBorderStyle;
  color?: string | null;
}

export interface DocumentCellStyle {
  font_family?: string | null;
  font_size?: number | null;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strikethrough?: boolean;
  /** Always `#RRGGBB` — themes and indexed palettes are resolved server-side. */
  text_color?: string | null;
  background_color?: string | null;
  horizontal?: DocumentHorizontalAlignment | null;
  vertical?: DocumentVerticalAlignment | null;
  wrap_text?: boolean;
  number_format?: string | null;
  border_top?: DocumentBorderEdge | null;
  border_bottom?: DocumentBorderEdge | null;
  border_left?: DocumentBorderEdge | null;
  border_right?: DocumentBorderEdge | null;
}

export interface DocumentCell {
  /** Zero-based. */
  row: number;
  column: number;
  /** Dates arrive as an Excel serial number; `type` says how to read it. */
  value?: string | number | boolean | null;
  formula?: string | null;
  type: DocumentCellType;
  /** Index into `WorkbookDocument.styles`. */
  style_id?: number | null;
}

export interface DocumentColumnMeta {
  index: number;
  /** Excel character-width units, not pixels. */
  width?: number | null;
  hidden?: boolean;
}

export interface DocumentRowMeta {
  index: number;
  /** Points, as Excel stores row heights. */
  height?: number | null;
  hidden?: boolean;
}

export interface DocumentWorksheet {
  name: string;
  index: number;
  hidden?: boolean;
  tab_color?: string | null;
  row_count: number;
  column_count: number;
  cells: DocumentCell[];
  /** A1 ranges, e.g. `A1:C3`. */
  merges: string[];
  columns: DocumentColumnMeta[];
  rows: DocumentRowMeta[];
  frozen_rows?: number;
  frozen_columns?: number;
  show_gridlines?: boolean;
}

export interface DocumentConversionWarning {
  code: string;
  message: string;
  sheet?: string | null;
  count: number;
}

export interface WorkbookDocument {
  schema_version: number;
  name: string;
  date_system: "1900" | "1904";
  sheets: DocumentWorksheet[];
  styles: DocumentCellStyle[];
  warnings: DocumentConversionWarning[];
}

export interface ImportedWorkbookResponse {
  filename: string;
  document: WorkbookDocument;
  sheet_count: number;
  cell_count: number;
}

/* ------------------------------------------------------------------ */
/* Unit conversion                                                     */
/* ------------------------------------------------------------------ */

/**
 * Excel measures column width in characters of the default font and row
 * height in points; Univer uses pixels for both. These are the conventional
 * approximations — exact only for an 11pt Calibri grid, which is what Excel
 * itself assumes when it writes the values.
 */
const PIXELS_PER_CHARACTER = 7;
const CELL_PADDING_PIXELS = 5;
const PIXELS_PER_POINT = 96 / 72;

export function characterWidthToPixels(width: number): number {
  return Math.round(width * PIXELS_PER_CHARACTER + CELL_PADDING_PIXELS);
}

export function pixelsToCharacterWidth(pixels: number): number {
  return (
    Math.round(
      ((pixels - CELL_PADDING_PIXELS) / PIXELS_PER_CHARACTER) * 100,
    ) / 100
  );
}

export function pointsToPixels(points: number): number {
  return Math.round(points * PIXELS_PER_POINT);
}

export function pixelsToPoints(pixels: number): number {
  return Math.round((pixels / PIXELS_PER_POINT) * 100) / 100;
}
