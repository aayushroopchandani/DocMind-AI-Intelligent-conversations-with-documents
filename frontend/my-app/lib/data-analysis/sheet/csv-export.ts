import { downloadBlob, safeFileName } from "@/lib/data-analysis/sheet/download";
import { withSheet } from "@/lib/data-analysis/sheet/sheet-api";

/**
 * CSV download for the active worksheet.
 *
 * Deliberately the only export that ships today: it is pure browser work
 * (read the used range, serialise, hand the blob to the browser), so it does
 * not owe anything to the analysis backend. XLSX round-tripping does — that
 * needs the server-side converter — and stays behind the pending notice.
 */

/** RFC 4180: quote when the value contains a delimiter, quote or newline. */
function escapeCsvCell(value: string): string {
  if (!/[",\r\n]/.test(value)) return value;
  return `"${value.replaceAll('"', '""')}"`;
}

function toCsv(rows: readonly (readonly string[])[]): string {
  return rows.map((row) => row.map(escapeCsvCell).join(",")).join("\r\n");
}

export interface CsvExportResult {
  fileName: string;
  rowCount: number;
}

/**
 * Serialises the active worksheet's used range to CSV and downloads it.
 * Returns null when there is no active sheet, or when it holds no data.
 */
export function exportActiveSheetToCsv(
  workbookName: string,
): CsvExportResult | null {
  const data = withSheet((sheet) => ({
    name: sheet.getSheetName(),
    // Display values, so dates and number formats export as the user sees
    // them rather than as raw serial numbers.
    values: sheet.getDataRange().getDisplayValues(),
  }));

  if (!data || data.values.length === 0) return null;
  // A blank sheet still reports a 1×1 used range; treat that as "no data".
  const isEmpty = data.values.every((row) =>
    row.every((cell) => cell.trim() === ""),
  );
  if (isEmpty) return null;

  const fileName = `${safeFileName(workbookName)} — ${safeFileName(data.name)}.csv`;
  // The BOM keeps Excel from mangling non-ASCII text on open.
  downloadBlob(
    fileName,
    new Blob([`\ufeff${toCsv(data.values)}`], {
      type: "text/csv;charset=utf-8",
    }),
  );
  return { fileName, rowCount: data.values.length };
}
