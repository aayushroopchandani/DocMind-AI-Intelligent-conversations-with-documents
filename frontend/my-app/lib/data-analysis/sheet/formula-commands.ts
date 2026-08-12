import { columnLabel } from "@/lib/data-analysis/range-label";
import {
  getSelectionBounds,
  withSheet,
  type SelectionBounds,
} from "@/lib/data-analysis/sheet/sheet-api";

/**
 * Formulas menu behaviour.
 *
 * Univer's own Formulas ribbon stays the full reference library; this menu
 * is the fast path — it writes a working formula against whatever the user
 * has selected instead of leaving an empty `=SUM()` to fill in by hand.
 *
 * The argument range is inferred the way spreadsheets have always done it:
 *   • a multi-cell selection is the argument, and the result lands just past
 *     it (below a column block, right of a row block);
 *   • a single cell scans upwards, then leftwards, for the contiguous run of
 *     populated cells feeding into it;
 *   • with nothing to aggregate, an empty call is written so the user can
 *     type the arguments straight into the cell.
 */

/** How far back a single-cell selection looks for a contiguous data run. */
const MAX_SCAN_DISTANCE = 1000;

function a1(row: number, column: number): string {
  return `${columnLabel(column)}${row + 1}`;
}

function rangeRef(
  startRow: number,
  startColumn: number,
  endRow: number,
  endColumn: number,
): string {
  return `${a1(startRow, startColumn)}:${a1(endRow, endColumn)}`;
}

interface FormulaPlacement {
  /** Where the formula is written. */
  row: number;
  column: number;
  /** The argument reference, or "" when nothing could be inferred. */
  reference: string;
}

function planFromMultiCellSelection(
  bounds: SelectionBounds,
): FormulaPlacement {
  const isColumnBlock = bounds.rowCount >= bounds.columnCount;
  const reference = rangeRef(
    bounds.startRow,
    bounds.startColumn,
    bounds.endRow,
    bounds.endColumn,
  );
  return isColumnBlock
    ? { row: bounds.endRow + 1, column: bounds.startColumn, reference }
    : { row: bounds.startRow, column: bounds.endColumn + 1, reference };
}

/**
 * Walks back from the active cell to find the block of populated cells it
 * sits under (or to the right of), the way Excel's AutoSum does.
 */
function planFromSingleCell(bounds: SelectionBounds): FormulaPlacement {
  const { startRow: row, startColumn: column } = bounds;
  const placement = { row, column, reference: "" };

  const runStart = withSheet((sheet) => {
    const limit = Math.max(0, row - MAX_SCAN_DISTANCE);
    let scan = row - 1;
    while (scan >= limit && !sheet.getRange(scan, column).isBlank()) scan -= 1;
    if (scan < row - 1) return { axis: "rows" as const, start: scan + 1 };

    const columnLimit = Math.max(0, column - MAX_SCAN_DISTANCE);
    let columnScan = column - 1;
    while (columnScan >= columnLimit && !sheet.getRange(row, columnScan).isBlank()) {
      columnScan -= 1;
    }
    if (columnScan < column - 1) {
      return { axis: "columns" as const, start: columnScan + 1 };
    }
    return null;
  });

  if (!runStart) return placement;
  return {
    ...placement,
    reference:
      runStart.axis === "rows"
        ? rangeRef(runStart.start, column, row - 1, column)
        : rangeRef(row, runStart.start, row, column - 1),
  };
}

export interface FormulaInsertResult {
  /** A1 address the formula was written to. */
  cell: string;
  /** The inferred argument range, or "" when none could be found. */
  reference: string;
}

/**
 * Writes `=NAME(range)` for the current selection.
 *
 * Returns where it landed and what it aggregated, so the caller can tell the
 * user that a bare `=NAME()` still needs its arguments — an empty call shows
 * as `#N/A` for anything that requires a range, and a silent error in a cell
 * is worse than a sentence explaining it.
 */
export function insertFunction(name: string): FormulaInsertResult | null {
  const bounds = getSelectionBounds();
  if (!bounds) return null;

  const plan = bounds.isSingleCell
    ? planFromSingleCell(bounds)
    : planFromMultiCellSelection(bounds);

  return withSheet((sheet) => {
    const target = sheet.getRange(plan.row, plan.column);
    target.setFormula(`=${name}(${plan.reference})`);
    target.activate();
    return { cell: a1(plan.row, plan.column), reference: plan.reference };
  });
}

/* ------------------------------------------------------------------ */
/* Function catalogue                                                  */
/* ------------------------------------------------------------------ */

export interface FunctionGroup {
  id: string;
  label: string;
  functions: readonly { name: string; hint: string }[];
}

/**
 * The shortlist only — Univer's Formulas ribbon owns the full library, and
 * duplicating five hundred entries here would help nobody.
 */
export const FUNCTION_GROUPS: readonly FunctionGroup[] = [
  {
    id: "aggregate",
    label: "Aggregate",
    functions: [
      { name: "SUM", hint: "Add the values" },
      { name: "AVERAGE", hint: "Arithmetic mean" },
      { name: "COUNT", hint: "Count numbers" },
      { name: "COUNTA", hint: "Count non-empty" },
      { name: "MIN", hint: "Smallest value" },
      { name: "MAX", hint: "Largest value" },
    ],
  },
  {
    id: "statistical",
    label: "Statistical",
    functions: [
      { name: "MEDIAN", hint: "Middle value" },
      { name: "STDEV", hint: "Sample deviation" },
      { name: "VAR", hint: "Sample variance" },
      { name: "SUMIF", hint: "Add what matches" },
      { name: "COUNTIF", hint: "Count what matches" },
      { name: "AVERAGEIF", hint: "Mean of matches" },
    ],
  },
  {
    id: "lookup",
    label: "Lookup",
    functions: [
      { name: "VLOOKUP", hint: "Look up by column" },
      { name: "HLOOKUP", hint: "Look up by row" },
      { name: "INDEX", hint: "Value at position" },
      { name: "MATCH", hint: "Position of value" },
      { name: "XLOOKUP", hint: "Flexible lookup" },
    ],
  },
  {
    id: "text",
    label: "Text",
    functions: [
      { name: "CONCATENATE", hint: "Join text" },
      { name: "LEFT", hint: "Leading characters" },
      { name: "RIGHT", hint: "Trailing characters" },
      { name: "TRIM", hint: "Strip extra spaces" },
      { name: "UPPER", hint: "Upper case" },
      { name: "TEXT", hint: "Format as text" },
    ],
  },
  {
    id: "logical",
    label: "Logical",
    functions: [
      { name: "IF", hint: "Branch on a test" },
      { name: "IFS", hint: "Several tests" },
      { name: "AND", hint: "All must hold" },
      { name: "OR", hint: "Any may hold" },
      { name: "IFERROR", hint: "Fallback on error" },
    ],
  },
  {
    id: "date",
    label: "Date and time",
    functions: [
      { name: "TODAY", hint: "Current date" },
      { name: "NOW", hint: "Current timestamp" },
      { name: "DATEDIF", hint: "Span between dates" },
      { name: "YEAR", hint: "Year part" },
      { name: "MONTH", hint: "Month part" },
    ],
  },
] as const;

/** The five offered directly under "AutoSum". */
export const AUTOSUM_FUNCTIONS = [
  "SUM",
  "AVERAGE",
  "COUNT",
  "MIN",
  "MAX",
] as const;
