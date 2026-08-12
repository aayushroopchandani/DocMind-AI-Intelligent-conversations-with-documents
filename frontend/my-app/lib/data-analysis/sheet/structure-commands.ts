import {
  getActiveWorkbook,
  getApi,
  getSelectionBounds,
  withRange,
  withSheet,
  withWorkbook,
} from "@/lib/data-analysis/sheet/sheet-api";

/**
 * Insert menu behaviour that needs no backend: rows, columns, cells and
 * worksheets. Counts follow the spreadsheet convention of "as many as you
 * selected" — selecting three rows and inserting adds three.
 *
 * `Dimension` comes off the live facade rather than `@univerjs/core` so the
 * always-mounted menu bar never pulls the engine into the first-load bundle.
 */

type ShiftAxis = "ROWS" | "COLUMNS";

function shiftDimension(axis: ShiftAxis) {
  return getApi()?.Enum.Dimension[axis] ?? null;
}

/* ------------------------------------------------------------------ */
/* Rows and columns                                                    */
/* ------------------------------------------------------------------ */

export function insertRowsAbove(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) =>
    sheet.insertRowsBefore(bounds.startRow, bounds.rowCount),
  );
}

export function insertRowsBelow(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) => sheet.insertRowsAfter(bounds.endRow, bounds.rowCount));
}

export function insertColumnsLeft(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) =>
    sheet.insertColumnsBefore(bounds.startColumn, bounds.columnCount),
  );
}

export function insertColumnsRight(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) =>
    sheet.insertColumnsAfter(bounds.endColumn, bounds.columnCount),
  );
}

export function deleteRows(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) => sheet.deleteRows(bounds.startRow, bounds.rowCount));
}

export function deleteColumns(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) =>
    sheet.deleteColumns(bounds.startColumn, bounds.columnCount),
  );
}

/* ------------------------------------------------------------------ */
/* Cells                                                               */
/* ------------------------------------------------------------------ */

export function insertCellsShiftDown(): void {
  const dimension = shiftDimension("ROWS");
  if (dimension === null) return;
  withRange((range) => range.insertCells(dimension));
}

export function insertCellsShiftRight(): void {
  const dimension = shiftDimension("COLUMNS");
  if (dimension === null) return;
  withRange((range) => range.insertCells(dimension));
}

export function deleteCellsShiftUp(): void {
  const dimension = shiftDimension("ROWS");
  if (dimension === null) return;
  withRange((range) => range.deleteCells(dimension));
}

export function deleteCellsShiftLeft(): void {
  const dimension = shiftDimension("COLUMNS");
  if (dimension === null) return;
  withRange((range) => range.deleteCells(dimension));
}

/* ------------------------------------------------------------------ */
/* Sizing                                                              */
/* ------------------------------------------------------------------ */

/** Fits every selected column to its widest visible value. */
export function autoFitColumns(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) =>
    sheet.autoResizeColumns(bounds.startColumn, bounds.columnCount),
  );
}

export function autoFitRows(): void {
  const bounds = getSelectionBounds();
  if (!bounds) return;
  withSheet((sheet) => sheet.autoResizeRows(bounds.startRow, bounds.rowCount));
}

/* ------------------------------------------------------------------ */
/* Worksheets                                                          */
/* ------------------------------------------------------------------ */

/**
 * Adds a worksheet to a workbook and brings it to the front, returning the
 * new sheet's name (or null if the workbook was not available).
 *
 * This is what the workspace "+ New blank spreadsheet" action resolves to
 * once a workbook exists — the workspace holds a single workbook, and extra
 * surfaces live inside it as sheets.
 *
 * `unitId` targets a specific workbook: the caller may have just switched
 * tabs, so "the active workbook" is not yet the one the user asked about.
 */
export function addWorksheet(unitId?: string): string | null {
  const workbook = unitId
    ? (getApi()?.getWorkbook(unitId) ?? null)
    : getActiveWorkbook();
  if (!workbook) return null;
  try {
    const sheet = workbook.insertSheet();
    sheet.activate();
    return sheet.getSheetName();
  } catch (error) {
    console.error("[data-analysis] Failed to add worksheet", error);
    return null;
  }
}

export function duplicateActiveWorksheet(): void {
  withWorkbook((workbook) => workbook.duplicateActiveSheet());
}

export function deleteActiveWorksheet(): boolean {
  // Univer refuses to remove the last remaining sheet; mirror that here so
  // the caller can explain instead of silently doing nothing.
  const removed = withWorkbook((workbook) => {
    if (workbook.getNumSheets() <= 1) return false;
    return workbook.deleteActiveSheet();
  });
  return removed ?? false;
}

export function renameActiveWorksheet(name: string): void {
  const trimmed = name.trim();
  if (!trimmed) return;
  withSheet((sheet) => sheet.setName(trimmed));
}

export function getActiveWorksheetName(): string | null {
  return withSheet((sheet) => sheet.getSheetName());
}

export function getWorksheetCount(): number {
  return withWorkbook((workbook) => workbook.getNumSheets()) ?? 0;
}
