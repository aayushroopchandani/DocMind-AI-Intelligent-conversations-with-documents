import { getUniverBridge } from "@/lib/data-analysis/univer-bridge";

export type WorkbookCellType =
  | "blank"
  | "string"
  | "number"
  | "boolean"
  | "date"
  | "formula"
  | "error";

export interface WorkbookRangeSnapshot {
  range_a1: string;
  values: Array<Array<string | number | boolean | null>>;
  formulas: Array<Array<string | null>>;
  cell_types: Array<Array<WorkbookCellType | null>>;
  number_formats: Array<Array<string | null>>;
  column_headers: string[];
  header_row_index: number | null;
  row_count: number;
  column_count: number;
  merged_ranges: string[];
  hidden_rows: number[];
  hidden_columns: number[];
}

export interface CapturedWorkbookContext {
  context: {
    workbook_id: string;
    workbook_name: string;
    client_revision: number;
    worksheet_id: string;
    worksheet_name: string;
    selected_range: string | null;
    used_range: string;
    snapshot_range: string;
    snapshot_hash: string;
    snapshot: WorkbookRangeSnapshot | null;
    snapshot_artifact_version_id: string | null;
    locale: string;
    timezone: string;
    captured_at: string;
  };
  snapshot: WorkbookRangeSnapshot;
  inline: boolean;
}

const INLINE_CELL_LIMIT = 25_000;
const INLINE_BYTE_LIMIT = 5 * 1024 * 1024;
const DATE_FORMAT = /(^|[^a-z])(y{2,4}|m{1,4}|d{1,4})([^a-z]|$)/i;

function normalizeA1(value: string): string {
  const cells = value.trim().replaceAll("$", "").split("!").at(-1) ?? "A1";
  return cells.includes(":") ? cells.toUpperCase() : `${cells.toUpperCase()}:${cells.toUpperCase()}`;
}

function columnNumber(label: string): number {
  return [...label].reduce((value, character) => value * 26 + character.charCodeAt(0) - 64, 0);
}

function columnLabel(number: number): string {
  let value = number;
  let label = "";
  while (value > 0) {
    value -= 1;
    label = String.fromCharCode(65 + (value % 26)) + label;
    value = Math.floor(value / 26);
  }
  return label;
}

function parseA1(value: string) {
  const match = /^([A-Z]{1,3})([1-9]\d*):([A-Z]{1,3})([1-9]\d*)$/.exec(normalizeA1(value));
  if (!match) throw new Error("The selected workbook range is invalid.");
  return {
    startColumn: columnNumber(match[1]),
    startRow: Number(match[2]),
    endColumn: columnNumber(match[3]),
    endRow: Number(match[4]),
  };
}

function unionA1(left: string, right: string): string {
  const a = parseA1(left);
  const b = parseA1(right);
  return `${columnLabel(Math.min(a.startColumn, b.startColumn))}${Math.min(a.startRow, b.startRow)}:${columnLabel(Math.max(a.endColumn, b.endColumn))}${Math.max(a.endRow, b.endRow)}`;
}

function primitive(value: unknown): string | number | boolean | null {
  if (value == null) return null;
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("The selected range contains a non-finite number.");
    return value;
  }
  return String(value);
}

function cellType(value: string | number | boolean | null, formula: string | null, format: string | null): WorkbookCellType {
  if (formula) return "formula";
  if (value == null || value === "") return "blank";
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return format && DATE_FORMAT.test(format) ? "date" : "number";
  return typeof value === "string" && value.startsWith("#") ? "error" : "string";
}

function detectHeader(values: WorkbookRangeSnapshot["values"]): { headers: string[]; row: number | null } {
  const first = values[0] ?? [];
  const headers = first.map((value) => typeof value === "string" ? value.trim() : "");
  if (
    headers.length > 0 &&
    headers.every(Boolean) &&
    new Set(headers.map((value) => value.toLocaleLowerCase())).size === headers.length
  ) {
    return { headers, row: 0 };
  }
  return { headers: [], row: null };
}

function canonicalNumber(value: number): string {
  if (Object.is(value, -0) || value === 0) return "0";
  if (Number.isInteger(value)) return BigInt(value).toString();
  const exponent = Math.floor(Math.log10(Math.abs(value)));
  if (exponent < -4 || exponent >= 17) {
    const [coefficient, rawExponent] = value.toExponential(16).toLowerCase().split("e");
    const trimmed = coefficient.replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
    const normalizedExponent = Number(rawExponent);
    const sign = normalizedExponent >= 0 ? "+" : "-";
    return `${trimmed}e${sign}${Math.abs(normalizedExponent).toString().padStart(2, "0")}`;
  }
  return value
    .toFixed(Math.max(0, 16 - exponent))
    .replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
}

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`).join(",")}}`;
}

async function snapshotHash(snapshot: WorkbookRangeSnapshot): Promise<string> {
  const payload = {
    schema_version: 1,
    range: snapshot.range_a1,
    rows: snapshot.row_count,
    columns: snapshot.column_count,
    values: snapshot.values.map((row) => row.map((value) => {
      if (value === null) return { t: "null", v: null };
      if (typeof value === "boolean") return { t: "boolean", v: value };
      if (typeof value === "number") return { t: "number", v: canonicalNumber(value) };
      return { t: "string", v: value };
    })),
    formulas: snapshot.formulas,
    cell_types: snapshot.cell_types,
    number_formats: snapshot.number_formats,
    column_headers: snapshot.column_headers,
    header_row_index: snapshot.header_row_index,
    merged_ranges: snapshot.merged_ranges,
    hidden_rows: snapshot.hidden_rows,
    hidden_columns: snapshot.hidden_columns,
  };
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(stableStringify(payload)));
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export async function captureWorkbookContext(args: {
  preferredRange: string | null;
  revision: number;
}): Promise<CapturedWorkbookContext> {
  const workbook = getUniverBridge().api?.getActiveWorkbook();
  const worksheet = workbook?.getActiveSheet();
  if (!workbook || !worksheet) throw new Error("The active workbook is not ready.");

  const dataRange = normalizeA1(worksheet.getDataRange().getA1Notation());
  const selectedRange = args.preferredRange ? normalizeA1(args.preferredRange) : null;
  const usedRange = selectedRange ? unionA1(dataRange, selectedRange) : dataRange;
  const snapshotRange = selectedRange ?? usedRange;
  const range = worksheet.getRange(snapshotRange);
  const rawValues = range.getRawValues();
  const rawFormulas = range.getFormulas();
  const cells = range.getCellDataGrid();
  const formatReader = range as typeof range & { getNumberFormats?: () => string[][] };
  const rawFormats = formatReader.getNumberFormats?.() ?? [];
  const parsed = parseA1(snapshotRange);
  const rowCount = parsed.endRow - parsed.startRow + 1;
  const columnCount = parsed.endColumn - parsed.startColumn + 1;
  const values = Array.from({ length: rowCount }, (_, row) =>
    Array.from({ length: columnCount }, (_, column) => primitive(rawValues[row]?.[column])),
  );
  const formulas = Array.from({ length: rowCount }, (_, row) =>
    Array.from({ length: columnCount }, (_, column) => rawFormulas[row]?.[column] || null),
  );
  const numberFormats = Array.from({ length: rowCount }, (_, row) =>
    Array.from({ length: columnCount }, (_, column) => {
      const explicit = rawFormats[row]?.[column];
      const model = cells[row]?.[column] as { s?: { n?: { pattern?: string } } } | null | undefined;
      return explicit || model?.s?.n?.pattern || null;
    }),
  );
  const header = detectHeader(values);
  const snapshot: WorkbookRangeSnapshot = {
    range_a1: snapshotRange,
    values,
    formulas,
    cell_types: values.map((row, rowIndex) => row.map((value, columnIndex) =>
      cellType(value, formulas[rowIndex][columnIndex], numberFormats[rowIndex][columnIndex]),
    )),
    number_formats: numberFormats,
    column_headers: header.headers,
    header_row_index: header.row,
    row_count: rowCount,
    column_count: columnCount,
    merged_ranges: worksheet.getMergedRanges().map((item) => normalizeA1(item.getA1Notation())),
    hidden_rows: [],
    hidden_columns: [],
  };
  const hash = await snapshotHash(snapshot);
  const encodedBytes = new TextEncoder().encode(JSON.stringify(snapshot)).byteLength;
  const inline = rowCount * columnCount <= INLINE_CELL_LIMIT && encodedBytes <= INLINE_BYTE_LIMIT;
  return {
    snapshot,
    inline,
    context: {
      workbook_id: workbook.getId(),
      workbook_name: workbook.getName(),
      client_revision: args.revision,
      worksheet_id: worksheet.getSheetId(),
      worksheet_name: worksheet.getSheetName(),
      selected_range: selectedRange,
      used_range: usedRange,
      snapshot_range: snapshotRange,
      snapshot_hash: hash,
      snapshot: inline ? snapshot : null,
      snapshot_artifact_version_id: null,
      locale: navigator.language || "en-US",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      captured_at: new Date().toISOString(),
    },
  };
}
