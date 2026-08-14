import type {
  ICellData,
  IRange,
  IStyleData,
  IWorkbookData,
  IWorksheetData,
} from "@univerjs/core";
import { columnLabel } from "@/lib/data-analysis/range-label";
import {
  characterWidthToPixels,
  pointsToPixels,
  type DocumentBorderEdge,
  type DocumentCell,
  type DocumentCellStyle,
  type DocumentWorksheet,
  type WorkbookDocument,
} from "@/lib/data-analysis/sheet/workbook-document";

/**
 * Interchange model to Univer worksheets.
 *
 * Univer's enums are plain numbers in the snapshot format, so they are
 * written as literals here rather than imported from `@univerjs/core`: this
 * module is reachable from the menu bar, and a value import would pull the
 * engine into the route's first-load bundle instead of its lazy chunk. The
 * values are part of the persisted file format and do not drift.
 */

const BOOLEAN_TRUE = 1;

/** `CellValueType` */
const VALUE_TYPE_STRING = 1;
const VALUE_TYPE_NUMBER = 2;
const VALUE_TYPE_BOOLEAN = 3;

/** `HorizontalAlign` */
const HORIZONTAL: Record<string, number> = {
  left: 1,
  center: 2,
  right: 3,
  justify: 4,
};

/** `VerticalAlign` */
const VERTICAL: Record<string, number> = { top: 1, middle: 2, bottom: 3 };

/** `WrapStrategy.WRAP` */
const WRAP_STRATEGY_WRAP = 3;

/** `BorderStyleTypes` */
const BORDER_STYLE: Record<string, number> = {
  none: 0,
  thin: 1,
  dotted: 3,
  dashed: 4,
  double: 7,
  medium: 8,
  thick: 13,
};

function borderSide(edge: DocumentBorderEdge | null | undefined) {
  if (!edge || edge.style === "none") return undefined;
  return {
    s: BORDER_STYLE[edge.style] ?? BORDER_STYLE.thin,
    cl: { rgb: edge.color ?? "#000000" },
  };
}

/** Interchange style to Univer's `IStyleData`. */
export function toUniverStyle(style: DocumentCellStyle): IStyleData {
  const univer: IStyleData = {};

  if (style.font_family) univer.ff = style.font_family;
  if (style.font_size) univer.fs = style.font_size;
  if (style.bold) univer.bl = BOOLEAN_TRUE;
  if (style.italic) univer.it = BOOLEAN_TRUE;
  if (style.underline) univer.ul = { s: BOOLEAN_TRUE };
  if (style.strikethrough) univer.st = { s: BOOLEAN_TRUE };
  if (style.text_color) univer.cl = { rgb: style.text_color };
  if (style.background_color) univer.bg = { rgb: style.background_color };

  const horizontal = style.horizontal ? HORIZONTAL[style.horizontal] : undefined;
  if (horizontal) univer.ht = horizontal;
  const vertical = style.vertical ? VERTICAL[style.vertical] : undefined;
  if (vertical) univer.vt = vertical;
  if (style.wrap_text) univer.tb = WRAP_STRATEGY_WRAP;
  if (style.number_format) univer.n = { pattern: style.number_format };

  const borders = {
    t: borderSide(style.border_top),
    b: borderSide(style.border_bottom),
    l: borderSide(style.border_left),
    r: borderSide(style.border_right),
  };
  if (Object.values(borders).some(Boolean)) univer.bd = borders;

  return univer;
}

function toUniverCell(
  cell: DocumentCell,
  styleId: string | undefined,
): ICellData {
  const data: ICellData = {};

  switch (cell.type) {
    case "formula":
      if (cell.formula) data.f = cell.formula;
      break;
    case "boolean":
      data.v = Boolean(cell.value);
      data.t = VALUE_TYPE_BOOLEAN;
      break;
    case "number":
    case "date":
      // A date is its serial number; the number format renders it.
      if (typeof cell.value === "number") {
        data.v = cell.value;
        data.t = VALUE_TYPE_NUMBER;
      }
      break;
    case "blank":
      break;
    default:
      if (cell.value != null) {
        data.v = String(cell.value);
        data.t = VALUE_TYPE_STRING;
      }
  }

  if (styleId) data.s = styleId;
  return data;
}

function parseA1Range(range: string): IRange | null {
  const match = /^([A-Z]{1,3})(\d+)(?::([A-Z]{1,3})(\d+))?$/i.exec(
    range.replaceAll("$", "").split("!").at(-1)?.toUpperCase() ?? "",
  );
  if (!match) return null;

  const toIndex = (label: string) =>
    [...label].reduce(
      (value, character) => value * 26 + character.charCodeAt(0) - 64,
      0,
    ) - 1;

  const startColumn = toIndex(match[1]);
  const startRow = Number(match[2]) - 1;
  return {
    startRow,
    startColumn,
    endRow: match[4] ? Number(match[4]) - 1 : startRow,
    endColumn: match[3] ? toIndex(match[3]) : startColumn,
  };
}

/** Room to keep typing past the imported data, as a new sheet would have. */
const MINIMUM_ROWS = 100;
const MINIMUM_COLUMNS = 26;

export interface ConvertedSheet {
  sheet: Partial<IWorksheetData>;
  styles: Record<string, IStyleData>;
}

/**
 * One interchange worksheet to a Univer worksheet.
 *
 * Styles are emitted into the workbook-level table with a caller-supplied
 * prefix so several imports can share one workbook without colliding.
 */
export function toUniverWorksheet(
  source: DocumentWorksheet,
  document: WorkbookDocument,
  options: { sheetId: string; name: string; stylePrefix: string },
): ConvertedSheet {
  const styles: Record<string, IStyleData> = {};
  const styleIds = new Map<number, string>();

  const styleIdFor = (index: number | null | undefined) => {
    if (index == null) return undefined;
    const existing = styleIds.get(index);
    if (existing) return existing;
    const source = document.styles[index];
    if (!source) return undefined;
    const id = `${options.stylePrefix}-${index}`;
    styles[id] = toUniverStyle(source);
    styleIds.set(index, id);
    return id;
  };

  const cellData: Record<number, Record<number, ICellData>> = {};
  for (const cell of source.cells) {
    const row = (cellData[cell.row] ??= {});
    row[cell.column] = toUniverCell(cell, styleIdFor(cell.style_id));
  }

  const rowData: Record<number, { h?: number; hd?: number }> = {};
  for (const row of source.rows) {
    const entry: { h?: number; hd?: number } = {};
    if (row.height != null) entry.h = pointsToPixels(row.height);
    if (row.hidden) entry.hd = BOOLEAN_TRUE;
    if (entry.h !== undefined || entry.hd !== undefined) rowData[row.index] = entry;
  }

  const columnData: Record<number, { w?: number; hd?: number }> = {};
  for (const column of source.columns) {
    const entry: { w?: number; hd?: number } = {};
    if (column.width != null) entry.w = characterWidthToPixels(column.width);
    if (column.hidden) entry.hd = BOOLEAN_TRUE;
    if (entry.w !== undefined || entry.hd !== undefined) {
      columnData[column.index] = entry;
    }
  }

  const mergeData = source.merges
    .map(parseA1Range)
    .filter((range): range is IRange => range !== null);

  const frozenRows = source.frozen_rows ?? 0;
  const frozenColumns = source.frozen_columns ?? 0;

  return {
    styles,
    sheet: {
      id: options.sheetId,
      name: options.name,
      rowCount: Math.max(source.row_count + MINIMUM_ROWS, MINIMUM_ROWS),
      columnCount: Math.max(
        source.column_count + MINIMUM_COLUMNS,
        MINIMUM_COLUMNS,
      ),
      hidden: source.hidden ? BOOLEAN_TRUE : 0,
      tabColor: source.tab_color ?? "",
      cellData,
      rowData,
      columnData,
      mergeData,
      showGridlines: source.show_gridlines === false ? 0 : 1,
      freeze: {
        xSplit: frozenColumns,
        ySplit: frozenRows,
        startRow: frozenRows,
        startColumn: frozenColumns,
      },
    },
  };
}

/**
 * Merge an imported document into an existing workbook snapshot.
 *
 * The workspace keeps a single workbook, so importing adds the file's sheets
 * to it rather than opening a second document. Names are suffixed on
 * collision, the way a file system would.
 */
export function mergeDocumentIntoWorkbook(
  workbook: Partial<IWorkbookData>,
  document: WorkbookDocument,
  options: { importId: string },
): Partial<IWorkbookData> {
  const sheets = { ...(workbook.sheets ?? {}) };
  const sheetOrder = [...(workbook.sheetOrder ?? [])];
  const styles = { ...((workbook.styles ?? {}) as Record<string, IStyleData>) };

  const takenNames = new Set(
    Object.values(sheets).map((sheet) => (sheet as IWorksheetData).name),
  );

  const uniqueName = (name: string) => {
    const base = name.trim() || "Sheet";
    if (!takenNames.has(base)) {
      takenNames.add(base);
      return base;
    }
    for (let suffix = 2; ; suffix += 1) {
      const candidate = `${base} (${suffix})`;
      if (!takenNames.has(candidate)) {
        takenNames.add(candidate);
        return candidate;
      }
    }
  };

  const ordered = [...document.sheets].sort((a, b) => a.index - b.index);
  ordered.forEach((source, position) => {
    const sheetId = `${options.importId}-${position}`;
    const converted = toUniverWorksheet(source, document, {
      sheetId,
      name: uniqueName(source.name),
      stylePrefix: `${options.importId}-${position}`,
    });
    sheets[sheetId] = converted.sheet as IWorksheetData;
    sheetOrder.push(sheetId);
    Object.assign(styles, converted.styles);
  });

  return { ...workbook, sheets, sheetOrder, styles };
}

/** Builds a brand-new workbook snapshot from an imported document. */
export function documentToWorkbook(
  document: WorkbookDocument,
  options: { workbookId: string; name: string },
): Partial<IWorkbookData> {
  return mergeDocumentIntoWorkbook(
    {
      id: options.workbookId,
      name: options.name,
      sheetOrder: [],
      sheets: {},
      styles: {},
    },
    document,
    { importId: "import-1" },
  );
}

/** `A1:D20` for a sheet's used range — used in import feedback. */
export function usedRangeLabel(sheet: DocumentWorksheet): string {
  if (!sheet.row_count || !sheet.column_count) return "A1";
  return `A1:${columnLabel(sheet.column_count - 1)}${sheet.row_count}`;
}
