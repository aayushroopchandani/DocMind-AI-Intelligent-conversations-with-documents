/**
 * The canonical cell hash, TypeScript side (Phase 9.10.4).
 *
 * This must produce byte-identical digests to
 * `backend/scripts/data_analysis_agent/runtime/patches/cells.py`. Both sides run
 * the same golden fixtures in `cell-hash.fixtures.ts`; if they ever diverge, a
 * patch compiled against one view of the sheet would be applied against
 * another, which is precisely the corruption the guards exist to prevent.
 *
 * The number canonicalization is the one already used by the workbook snapshot
 * hash, so a cell digests identically whether it arrives through a snapshot or
 * through a patch guard.
 */

export const CELL_HASH_VERSION = "1.0";

export type CellPrimitive = string | number | boolean | null;

export type WorkbookCellType = "string" | "number" | "boolean" | "date" | "blank";

export interface CellState {
  value: CellPrimitive;
  formula: string | null;
  cellType: WorkbookCellType | null;
  numberFormat: string | null;
  merged: boolean;
  protected: boolean;
}

export const BLANK_CELL: CellState = {
  value: null,
  formula: null,
  cellType: null,
  numberFormat: null,
  merged: false,
  protected: false,
};

/**
 * Whether a cell counts as empty for collision purposes.
 *
 * Formatting alone does not make a cell occupied: a styled but empty target is
 * still safe to write into.
 */
export function isBlankCell(cell: CellState): boolean {
  return cell.value === null && !cell.formula && !cell.merged && !cell.protected;
}

/** Shared with the snapshot hash; keep in step with `_canonical_number`. */
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

function canonicalValue(value: CellPrimitive): { t: string; v: unknown } {
  if (value === null) return { t: "null", v: null };
  if (typeof value === "boolean") return { t: "boolean", v: value };
  if (typeof value === "number") return { t: "number", v: canonicalNumber(value) };
  return { t: "string", v: value };
}

export function canonicalCellPayload(cell: CellState): Record<string, unknown> {
  return {
    v: canonicalValue(cell.value),
    f: cell.formula || null,
    t: cell.cellType ?? null,
    n: cell.numberFormat || null,
    m: cell.merged,
    p: cell.protected,
  };
}

/** Key-sorted, separator-free JSON, matching Python's canonical dump. */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`)
    .join(",")}}`;
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function cellHash(cell: CellState): Promise<string> {
  return sha256(
    stableStringify({ schema_version: CELL_HASH_VERSION, cell: canonicalCellPayload(cell) }),
  );
}

/**
 * Digest for a rectangle. The range is part of the hash, so the same content at
 * a different address does not satisfy a guard.
 */
export async function rangeHash(rangeA1: string, cells: CellState[][]): Promise<string> {
  const rows = cells.length;
  const columns = rows > 0 ? cells[0].length : 0;
  if (cells.some((row) => row.length !== columns)) {
    throw new Error("cell grid must be rectangular");
  }
  return sha256(
    stableStringify({
      schema_version: CELL_HASH_VERSION,
      range: rangeA1,
      rows,
      columns,
      cells: cells.map((row) => row.map(canonicalCellPayload)),
    }),
  );
}

/** The digest a genuinely empty rectangle produces. */
export async function blankRangeHash(
  rangeA1: string,
  rows: number,
  columns: number,
): Promise<string> {
  const cells = Array.from({ length: rows }, () =>
    Array.from({ length: columns }, () => BLANK_CELL),
  );
  return rangeHash(rangeA1, cells);
}
