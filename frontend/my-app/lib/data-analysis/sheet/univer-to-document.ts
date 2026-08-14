import type {
  IBorderStyleData,
  ICellData,
  IColorStyle,
  IStyleData,
  IWorkbookData,
  IWorksheetData,
} from "@univerjs/core";
import { formatRangeA1 } from "@/lib/data-analysis/range-label";
import {
  pixelsToCharacterWidth,
  pixelsToPoints,
  WORKBOOK_DOCUMENT_SCHEMA_VERSION,
  type DocumentBorderEdge,
  type DocumentBorderStyle,
  type DocumentCell,
  type DocumentCellStyle,
  type DocumentCellType,
  type DocumentHorizontalAlignment,
  type DocumentVerticalAlignment,
  type DocumentWorksheet,
  type WorkbookDocument,
} from "@/lib/data-analysis/sheet/workbook-document";

/**
 * Univer workbook snapshot to the interchange model, for export.
 *
 * The snapshot from `workbook.save()` is the right source: it already holds
 * the resolved style table, merges, row/column metadata and freeze state, so
 * export never has to walk the grid cell by cell through the facade.
 *
 * Univer enum values are compared as the plain numbers they are stored as —
 * see the note in `document-to-univer.ts` for why they are not imported.
 */

const HORIZONTAL: Record<number, DocumentHorizontalAlignment> = {
  1: "left",
  2: "center",
  3: "right",
  4: "justify",
};

const VERTICAL: Record<number, DocumentVerticalAlignment> = {
  1: "top",
  2: "middle",
  3: "bottom",
};

const BORDER_STYLE: Record<number, DocumentBorderStyle> = {
  0: "none",
  1: "thin",
  2: "thin",
  3: "dotted",
  4: "dashed",
  5: "dashed",
  6: "dashed",
  7: "double",
  8: "medium",
  9: "dashed",
  10: "dashed",
  11: "dashed",
  12: "dashed",
  13: "thick",
};

const WRAP_STRATEGY_WRAP = 3;
const VALUE_TYPE_NUMBER = 2;
const VALUE_TYPE_BOOLEAN = 3;

/** Number formats Excel treats as dates, so export can label the cell type. */
const DATE_PATTERN = /(^|[^a-z\\])(y{2,4}|m{1,5}|d{1,4}|h{1,2}|s{1,2})([^a-z]|$)/i;

// Univer's `Nullable<T>` also admits `void`, so the value is narrowed by
// type rather than by truthiness alone.
function normalizeColor(color: IColorStyle | null | undefined): string | null {
  const value = color?.rgb;
  if (typeof value !== "string" || !value) return null;
  if (value.startsWith("#")) {
    return value.length === 7 ? value.toUpperCase() : null;
  }
  if (/^[0-9a-f]{6}$/i.test(value)) return `#${value.toUpperCase()}`;
  if (/^[0-9a-f]{8}$/i.test(value)) return `#${value.slice(-6).toUpperCase()}`;
  // Univer also accepts CSS colour names and rgb(); those cannot be mapped
  // to the ARGB form Excel needs, so they are dropped rather than guessed.
  return null;
}

function borderEdge(
  side: IBorderStyleData | null | undefined,
): DocumentBorderEdge | null {
  if (!side || typeof side.s !== "number") return null;
  const style = BORDER_STYLE[side.s] ?? "thin";
  if (style === "none") return null;
  return { style, color: normalizeColor(side.cl) };
}

function toDocumentStyle(style: IStyleData): DocumentCellStyle {
  const borders = style.bd ?? {};
  return {
    font_family: style.ff ?? null,
    font_size: style.fs ?? null,
    bold: style.bl === 1,
    italic: style.it === 1,
    underline: style.ul?.s === 1,
    strikethrough: style.st?.s === 1,
    text_color: normalizeColor(style.cl ?? undefined),
    background_color: normalizeColor(style.bg ?? undefined),
    horizontal: style.ht ? (HORIZONTAL[style.ht] ?? null) : null,
    vertical: style.vt ? (VERTICAL[style.vt] ?? null) : null,
    wrap_text: style.tb === WRAP_STRATEGY_WRAP,
    number_format: style.n?.pattern ?? null,
    border_top: borderEdge(borders.t ?? undefined),
    border_bottom: borderEdge(borders.b ?? undefined),
    border_left: borderEdge(borders.l ?? undefined),
    border_right: borderEdge(borders.r ?? undefined),
  };
}

function isEmptyStyle(style: DocumentCellStyle): boolean {
  return Object.values(style).every(
    (value) => value === null || value === false || value === undefined,
  );
}

/** Interns styles so the exported document carries one entry per style. */
class StyleCollector {
  private readonly indices = new Map<string, number>();
  readonly styles: DocumentCellStyle[] = [];

  add(style: DocumentCellStyle): number | null {
    if (isEmptyStyle(style)) return null;
    const key = JSON.stringify(style);
    const existing = this.indices.get(key);
    if (existing !== undefined) return existing;
    const index = this.styles.length;
    this.styles.push(style);
    this.indices.set(key, index);
    return index;
  }
}

function cellType(cell: ICellData, style: DocumentCellStyle): DocumentCellType {
  if (cell.f) return "formula";
  if (cell.v === undefined || cell.v === null || cell.v === "") return "blank";
  if (cell.t === VALUE_TYPE_BOOLEAN || typeof cell.v === "boolean") {
    return "boolean";
  }
  if (cell.t === VALUE_TYPE_NUMBER || typeof cell.v === "number") {
    return style.number_format && DATE_PATTERN.test(style.number_format)
      ? "date"
      : "number";
  }
  return typeof cell.v === "string" && cell.v.startsWith("#")
    ? "error"
    : "string";
}

function toDocumentSheet(
  sheet: IWorksheetData,
  index: number,
  workbookStyles: Record<string, IStyleData>,
  collector: StyleCollector,
): DocumentWorksheet {
  const cells: DocumentCell[] = [];
  let maxRow = 0;
  let maxColumn = 0;

  for (const [rowKey, columns] of Object.entries(sheet.cellData ?? {})) {
    const row = Number(rowKey);
    for (const [columnKey, cell] of Object.entries(
      columns as Record<string, ICellData>,
    )) {
      const column = Number(columnKey);
      if (!cell) continue;

      // A cell's style is either an id into the workbook table or inline.
      const resolved: IStyleData =
        typeof cell.s === "string"
          ? (workbookStyles[cell.s] ?? {})
          : ((cell.s as IStyleData | undefined) ?? {});
      const style = toDocumentStyle(resolved);
      const type = cellType(cell, style);
      const styleId = collector.add(style);
      if (type === "blank" && styleId === null) continue;

      cells.push({
        row,
        column,
        value: type === "formula" ? null : ((cell.v as DocumentCell["value"]) ?? null),
        formula: cell.f ?? null,
        type,
        style_id: styleId,
      });
      maxRow = Math.max(maxRow, row + 1);
      maxColumn = Math.max(maxColumn, column + 1);
    }
  }

  const rows = Object.entries(sheet.rowData ?? {})
    .map(([key, data]) => {
      const meta = data as { h?: number; hd?: number };
      return {
        index: Number(key),
        height: meta.h != null ? pixelsToPoints(meta.h) : null,
        hidden: meta.hd === 1,
      };
    })
    .filter((row) => row.height != null || row.hidden);

  const columns = Object.entries(sheet.columnData ?? {})
    .map(([key, data]) => {
      const meta = data as { w?: number; hd?: number };
      return {
        index: Number(key),
        width: meta.w != null ? pixelsToCharacterWidth(meta.w) : null,
        hidden: meta.hd === 1,
      };
    })
    .filter((column) => column.width != null || column.hidden);

  return {
    name: sheet.name,
    index,
    hidden: sheet.hidden === 1,
    tab_color: sheet.tabColor || null,
    row_count: maxRow,
    column_count: maxColumn,
    cells,
    merges: (sheet.mergeData ?? []).map(formatRangeA1),
    columns,
    rows,
    frozen_rows: Math.max(0, sheet.freeze?.ySplit ?? 0),
    frozen_columns: Math.max(0, sheet.freeze?.xSplit ?? 0),
    show_gridlines: sheet.showGridlines !== 0,
  };
}

/** Convert a saved Univer workbook into the interchange model. */
export function workbookToDocument(
  workbook: Partial<IWorkbookData>,
  options: { name: string },
): WorkbookDocument {
  const collector = new StyleCollector();
  const styles = (workbook.styles ?? {}) as Record<string, IStyleData>;
  const sheets = (workbook.sheets ?? {}) as Record<string, IWorksheetData>;
  const order = workbook.sheetOrder ?? Object.keys(sheets);

  const converted = order
    .map((sheetId, index) => {
      const sheet = sheets[sheetId];
      return sheet ? toDocumentSheet(sheet, index, styles, collector) : null;
    })
    .filter((sheet): sheet is DocumentWorksheet => sheet !== null);

  return {
    schema_version: WORKBOOK_DOCUMENT_SCHEMA_VERSION,
    name: options.name,
    date_system: "1900",
    sheets: converted,
    styles: collector.styles,
    warnings: [],
  };
}
