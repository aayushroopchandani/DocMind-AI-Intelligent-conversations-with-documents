/**
 * Turning a published result sample into something renderable (Phase 9.9.1).
 *
 * The sample that arrives is deliberately small — capped server-side at twenty
 * rows and four hundred cells — and already redacted through the privacy
 * gateway. So there is no filtering or truncation to do here, only presentation:
 * join the sample's column keys to the schema so headers read as labels rather
 * than identifiers, format each value for its declared type, and say plainly
 * how much of the result is on screen.
 *
 * Kept out of the component so the formatting is testable on its own, and so
 * the component stays a renderer.
 *
 * All formatting goes through `lib/data-analysis/format`, which pins a locale:
 * the same result rendered on the server and rehydrated in the browser has to
 * produce identical text, and the runtime default does not.
 */

import type {
  PlanColumn,
  PlanDataType,
  ResultPreview,
} from "@/lib/data-analysis/execution/execution-types";
import {
  formatCount,
  formatInstant,
  numberFormatter,
  type NumberStyle,
} from "@/lib/data-analysis/format";

/** Numbers line up on the right; everything else reads from the left. */
export type PreviewAlignment = "start" | "end";

export interface PreviewColumn {
  key: string;
  /** The schema's label when known, otherwise the raw key. */
  label: string;
  /** Unit shown once in the header rather than repeated on every cell. */
  unit: string | null;
  dataType: PlanDataType | null;
  align: PreviewAlignment;
  /** The privacy gateway replaced this column's values before publication. */
  redacted: boolean;
}

export interface PreviewCell {
  text: string;
  align: PreviewAlignment;
  /** Empty or withheld: rendered dimmer than real data. */
  muted: boolean;
}

export interface PreviewTable {
  columns: PreviewColumn[];
  rows: PreviewCell[][];
  shownRows: number;
  totalRows: number;
  truncated: boolean;
  /** One line a reader can trust about how much they are looking at. */
  summary: string;
  redactedColumnCount: number;
}

const NUMERIC_TYPES: ReadonlySet<PlanDataType> = new Set<PlanDataType>([
  "integer",
  "decimal",
  "currency",
  "percentage",
]);

/** Shown for a value that is absent, rather than an empty-looking cell. */
const EMPTY = "—";

const REDACTED = "[redacted]";

function alignmentFor(dataType: PlanDataType | null): PreviewAlignment {
  return dataType !== null && NUMERIC_TYPES.has(dataType) ? "end" : "start";
}

/** The number style a column's declared type calls for. */
function numberStyleFor(dataType: PlanDataType | null): NumberStyle {
  if (dataType === "integer") return "count";
  if (dataType === "currency") return "money";
  return "decimal";
}

function formatCell(
  value: string | number | boolean | null | undefined,
  column: PreviewColumn,
  numbers: Intl.NumberFormat,
): PreviewCell {
  const { align } = column;

  if (column.redacted) return { text: REDACTED, align, muted: true };
  if (value === null || value === undefined) return { text: EMPTY, align, muted: true };

  if (typeof value === "boolean") {
    return { text: value ? "true" : "false", align, muted: false };
  }

  if (typeof value === "number") {
    if (!Number.isFinite(value)) return { text: EMPTY, align, muted: true };
    const text = numbers.format(value);
    return {
      text: column.dataType === "percentage" ? `${text}%` : text,
      align,
      muted: false,
    };
  }

  if (value === "") return { text: EMPTY, align, muted: true };

  if (column.dataType === "date" || column.dataType === "datetime") {
    return {
      text: formatInstant(value, column.dataType === "datetime"),
      align,
      muted: false,
    };
  }

  // The publisher already caps text length and neutralises anything that could
  // go live in a grid, so this is shown as it arrived.
  return { text: value, align, muted: false };
}

function describe(shown: number, total: number, truncated: boolean): string {
  if (total === 0) return "No rows";
  const noun = total === 1 ? "row" : "rows";
  if (!truncated) return `${formatCount(total)} ${noun}`;
  return `Showing ${formatCount(shown)} of ${formatCount(total)} ${noun}`;
}

/**
 * Build the renderable table.
 *
 * `schema` is the execution's `result_columns` when it has been fetched. It is
 * optional because the sample stands on its own: without it the table still
 * renders, using raw keys as headers and treating every value as text. That
 * matters because the preview and the execution are two separate calls, and one
 * may arrive first.
 */
export function buildPreviewTable(
  preview: ResultPreview,
  schema: readonly PlanColumn[] = [],
): PreviewTable {
  const byKey = new Map(schema.map((column) => [column.key, column]));
  const redacted = new Set(preview.redacted_column_keys);

  const columns: PreviewColumn[] = preview.columns.map((key) => {
    const declared = byKey.get(key);
    const dataType = declared?.data_type ?? null;
    return {
      key,
      label: declared?.label ?? key,
      unit: declared?.unit ?? null,
      dataType,
      align: alignmentFor(dataType),
      redacted: redacted.has(key),
    };
  });

  const formatters = columns.map((column) =>
    numberFormatter(numberStyleFor(column.dataType)),
  );
  const rows = preview.rows.map((row) =>
    columns.map((column, index) => formatCell(row[column.key], column, formatters[index])),
  );

  return {
    columns,
    rows,
    shownRows: preview.preview_row_count,
    totalRows: preview.row_count,
    truncated: preview.truncated,
    summary: describe(preview.preview_row_count, preview.row_count, preview.truncated),
    redactedColumnCount: columns.filter((column) => column.redacted).length,
  };
}

/** Header text for a column, with its unit when the schema declared one. */
export function columnHeading(column: PreviewColumn): string {
  return column.unit ? `${column.label} (${column.unit})` : column.label;
}

/** A compact "24 rows × 4 columns" line for a result summary. */
export function describeResultShape(
  rowCount: number | null,
  columnCount: number | null,
): string | null {
  if (rowCount === null) return null;
  const rows = `${formatCount(rowCount)} ${rowCount === 1 ? "row" : "rows"}`;
  if (columnCount === null) return rows;
  const columns = `${formatCount(columnCount)} ${columnCount === 1 ? "column" : "columns"}`;
  return `${rows} × ${columns}`;
}

/** Bytes as a short human string, for the published bundle size. */
export function describeBytes(bytes: number | null): string | null {
  if (bytes === null || bytes < 0) return null;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
