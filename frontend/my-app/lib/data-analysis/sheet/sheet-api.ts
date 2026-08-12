import type { FRange, FWorkbook, FWorksheet } from "@univerjs/sheets/facade";
import { getUniverBridge } from "@/lib/data-analysis/univer-bridge";

/**
 * Defensive accessors over the live Univer facade.
 *
 * Every menu command in `lib/data-analysis/sheet/*` funnels through here so
 * that a command can never throw into React: the engine may be unmounted
 * (a PDF tab is in front), still booting, or the active unit may have been
 * disposed mid-interaction. In all of those cases the accessors return
 * `null` and the command becomes a no-op — menu items are disabled through
 * `spreadsheetReady` long before that matters, so a silent skip is correct.
 */

type UniverApi = NonNullable<ReturnType<typeof getUniverBridge>["api"]>;

/** The facade, or null while Univer is unmounted. */
export function getApi(): UniverApi | null {
  return getUniverBridge().api;
}

export function getActiveWorkbook(): FWorkbook | null {
  try {
    return getApi()?.getActiveWorkbook() ?? null;
  } catch {
    return null;
  }
}

export function getActiveSheet(): FWorksheet | null {
  try {
    return getActiveWorkbook()?.getActiveSheet() ?? null;
  } catch {
    return null;
  }
}

/** The user's current selection, or the active cell when nothing is dragged. */
export function getActiveRange(): FRange | null {
  try {
    const sheet = getActiveSheet();
    return sheet?.getActiveRange() ?? sheet?.getActiveCell() ?? null;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* Runners                                                             */
/* ------------------------------------------------------------------ */

/** Runs `fn` against the active worksheet; returns null if unavailable. */
export function withSheet<T>(
  fn: (sheet: FWorksheet, workbook: FWorkbook) => T,
): T | null {
  const workbook = getActiveWorkbook();
  const sheet = workbook?.getActiveSheet();
  if (!workbook || !sheet) return null;
  try {
    return fn(sheet, workbook);
  } catch (error) {
    console.error("[data-analysis] Sheet command failed", error);
    return null;
  }
}

/** Runs `fn` against the current selection; returns null if unavailable. */
export function withRange<T>(fn: (range: FRange, sheet: FWorksheet) => T): T | null {
  const sheet = getActiveSheet();
  const range = getActiveRange();
  if (!sheet || !range) return null;
  try {
    return fn(range, sheet);
  } catch (error) {
    console.error("[data-analysis] Range command failed", error);
    return null;
  }
}

/** Runs `fn` against the whole workbook; returns null if unavailable. */
export function withWorkbook<T>(fn: (workbook: FWorkbook) => T): T | null {
  const workbook = getActiveWorkbook();
  if (!workbook) return null;
  try {
    return fn(workbook);
  } catch (error) {
    console.error("[data-analysis] Workbook command failed", error);
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* Selection geometry                                                  */
/* ------------------------------------------------------------------ */

export interface SelectionBounds {
  startRow: number;
  endRow: number;
  startColumn: number;
  endColumn: number;
  rowCount: number;
  columnCount: number;
  isSingleCell: boolean;
}

/** Zero-based, inclusive bounds of the current selection. */
export function getSelectionBounds(): SelectionBounds | null {
  return withRange((range) => {
    const startRow = range.getRow();
    const endRow = range.getLastRow();
    const startColumn = range.getColumn();
    const endColumn = range.getLastColumn();
    return {
      startRow,
      endRow,
      startColumn,
      endColumn,
      rowCount: endRow - startRow + 1,
      columnCount: endColumn - startColumn + 1,
      isSingleCell: startRow === endRow && startColumn === endColumn,
    };
  });
}
